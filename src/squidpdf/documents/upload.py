from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterable, Awaitable
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

from squidpdf.core import Reply, Workers, words
from squidpdf.documents import constants, disk, store
from squidpdf.documents.analyse import analyse_upload, judge_pages, keep_judged
from squidpdf.documents.errors import NotAPdf, TooLarge
from squidpdf.documents.meters import Meter
from squidpdf.documents.replies import document_json
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import JudgedPages, KeptAnalysis

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB
_DIGEST_CHARS = 16  # of a file's SHA-256 a meter keeps: 64 bits, too many to meet by chance


@dataclass(frozen=True, slots=True, eq=False)
class UploadController:
    workers: Workers
    documents: DocumentStore

    async def upload(
        self,
        owner_digest: str,
        *,
        meter: Meter,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        language: str,
    ) -> Reply[bytes]:
        declared_too_large = declared is not None and declared > constants.MAX_FILE_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FILE_MB)
        async with disk.disk_held(declared, most=constants.MAX_FILE_BYTES):
            with disk.full_disk_refused():
                doc_id, folder = await asyncio.to_thread(self.documents.create, owner_digest)
                await self._deleted_if_it_fails(
                    folder, _save_original(chunks, documents=self.documents, folder=folder)
                )
        # Deleted if it fails too: a browser that leaves first never gets the id.
        return await self._deleted_if_it_fails(
            folder, self._judged(doc_id, folder, meter=meter, language=language)
        )

    async def _judged(
        self, doc_id: str, folder: Path, *, meter: Meter, language: str
    ) -> Reply[bytes]:
        digest = await asyncio.to_thread(_digest_of, folder / store.ORIGINAL)
        await asyncio.to_thread(meter.check, digest)
        kept = await self._analysed(folder)
        expires_at = await asyncio.to_thread(self.documents.touch, folder)
        body = await asyncio.to_thread(
            document_json, doc_id, expires_at=expires_at, kept=kept, language=language
        )
        # Counted only once it opens, so a damaged file costs none; a refusal here deletes it.
        counted = await asyncio.to_thread(meter.count, digest)
        return Reply(body, words.language_headers(language) | counted, HTTPStatus.CREATED)

    async def _deleted_if_it_fails[T](self, folder: Path, step: Awaitable[T]) -> T:
        try:
            return await step
        except BaseException:
            await asyncio.to_thread(self.documents.delete, folder)
            raise

    async def _analysed(self, folder: Path) -> KeptAnalysis:
        analysed = await self._enqueue_analyse_upload(folder)
        if isinstance(analysed, KeptAnalysis):
            return analysed
        judged = await self._each_judged(folder, analysed)
        return await asyncio.to_thread(keep_judged, folder, judged)

    async def _each_judged(self, folder: Path, shares: list[range]) -> list[JudgedPages]:
        judging = [
            asyncio.ensure_future(self._enqueue_judge_pages(folder, pages=share))
            for share in shares
        ]
        try:
            return await asyncio.gather(*judging)
        except BaseException:
            for share in judging:
                share.cancel()
            await asyncio.gather(*judging, return_exceptions=True)
            raise

    async def _enqueue_analyse_upload(self, folder: Path) -> KeptAnalysis | list[range]:
        task = partial(
            analyse_upload,
            self.documents,
            folder,
            constants.MAX_PAGES,
            most_shares=constants.ANALYSE_WORKERS,
            spans_per_share=constants.SPANS_PER_SHARE,
        )
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)

    async def _enqueue_judge_pages(self, folder: Path, *, pages: range) -> JudgedPages:
        task = partial(judge_pages, self.documents, folder, pages)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _digest_of(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()[:_DIGEST_CHARS]


async def _save_original(
    chunks: AsyncIterable[bytes], *, documents: DocumentStore, folder: Path
) -> None:
    size, first_kb = 0, b""
    out = await asyncio.to_thread(documents.open_to_write, folder)
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > constants.MAX_FILE_BYTES:
                raise TooLarge(constants.MAX_FILE_MB)
            first_kb += chunk[: _HEADER_WINDOW - len(first_kb)]
            no_header = len(first_kb) == _HEADER_WINDOW and _PDF_HEADER not in first_kb
            if no_header:
                raise NotAPdf()
            await asyncio.to_thread(out.write, chunk)
    finally:
        await asyncio.to_thread(out.close)
    if _PDF_HEADER not in first_kb:
        raise NotAPdf()
