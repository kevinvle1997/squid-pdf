"""Upload: the file streamed to disk, checked, kept and judged. No web framework here."""

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
from squidpdf.documents.analyse import analyse_upload
from squidpdf.documents.errors import NotAPdf, TooLarge
from squidpdf.documents.meters import Meter
from squidpdf.documents.replies import document_json
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import KeptAnalysis

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB
_DIGEST_CHARS = 16  # of a file's SHA-256 a meter keeps: 64 bits, too many to meet by chance


@dataclass(frozen=True, slots=True, eq=False)
class UploadController:
    """Upload, from request to reply."""

    workers: Workers  # where it analyses, off the server's own thread
    documents: DocumentStore  # where its document is kept, and what opens it

    async def upload(
        self,
        owner_digest: str,
        *,
        meter: Meter,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        language: str,
    ) -> Reply[bytes]:
        """Keep `chunks` as a new document for this owner, and answer with every span judged.

        `declared` is the size stated, or None. Refuses a file too large, not a PDF, or with the
        disk nearly full, and a new one `meter` has no room for; counts one that opens.
        """
        # Read as module attributes, so a test can lower the limit.
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
        """The kept original, checked against `meter`, judged and counted: the reply to send."""
        # Off the server's thread: a file of the size limit takes a moment to digest.
        digest = await asyncio.to_thread(_digest_of, folder / store.ORIGINAL)
        # Refused before its analysis; the count refuses again, should another take the last.
        await asyncio.to_thread(meter.check, digest)
        kept = await self._enqueue_analyse(folder)
        expires_at = await asyncio.to_thread(self.documents.touch, folder)
        # Off the server's thread: it reads every font's letters and writes them out again.
        body = await asyncio.to_thread(
            document_json, doc_id, expires_at=expires_at, kept=kept, language=language
        )
        # Counted only once it opens, so a damaged file costs none; a refusal here deletes it.
        counted = await asyncio.to_thread(meter.count, digest)
        return Reply(body, words.language_headers(language) | counted, HTTPStatus.CREATED)

    async def _deleted_if_it_fails[T](self, folder: Path, step: Awaitable[T]) -> T:
        """What `step` gives; if it fails, the document in `folder` is deleted first."""
        try:
            return await step
        except BaseException:  # refused, damaged, or the browser left: keep nothing
            await asyncio.to_thread(self.documents.delete, folder)
            raise

    async def _enqueue_analyse(self, folder: Path) -> KeptAnalysis:
        """Analyse the document on a worker."""
        task = partial(analyse_upload, self.documents, folder, constants.MAX_PAGES)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _digest_of(path: Path) -> str:
    """The file at `path` by its contents, as a meter counts it: one file, one digest."""
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()[:_DIGEST_CHARS]


async def _save_original(
    chunks: AsyncIterable[bytes], *, documents: DocumentStore, folder: Path
) -> None:
    """Stream the upload to its original in `folder`, refused once too big or plainly no PDF.

    Opened, written and closed off the server's thread: a disk can be slow.
    """
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
    finally:  # closed, refused or not; the caller deletes what's refused
        await asyncio.to_thread(out.close)
    if _PDF_HEADER not in first_kb:
        raise NotAPdf()
