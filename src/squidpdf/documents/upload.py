"""Upload: the file streamed to disk, checked, kept and judged. No web framework here."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from functools import partial
from pathlib import Path

from squidpdf.core import Reply, Workers, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.errors import NotAPdf, TooLarge
from squidpdf.documents.info import document_response
from squidpdf.documents.types import Analysis, Document

__all__ = [
    "UploadController",
]

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB


class UploadController:
    """Upload, from request to reply."""

    def __init__(self, workers: Workers) -> None:
        """Analyse on `workers`, off the server's own thread."""
        self._workers = workers

    async def upload(
        self,
        owner_digest: str,
        *,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        said_in: str,
    ) -> Reply[Document]:
        """Keep `chunks` as a new document for this owner, and answer with every span judged.

        `declared` is the size the request states, None when it streams without
        one. Refuses a file too large or not a PDF, and keeps nothing then.
        """
        # Read as module attributes, so a test can lower the limit.
        declared_too_large = declared is not None and declared > constants.MAX_FILE_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FILE_MB)
        doc_id, folder = store.create(owner_digest)
        try:
            await save_original(chunks, to=folder / store.ORIGINAL)
            analysis = await self._enqueue_analyse(folder)
        except BaseException:  # refused, damaged, or the browser left: keep nothing
            await asyncio.to_thread(store.delete, folder)
            raise
        body = document_response(
            doc_id, expires_at=store.touch(folder), analysis=analysis, said_in=said_in
        )
        return Reply(body, words.language_headers(said_in))

    async def _enqueue_analyse(self, folder: Path) -> Analysis:
        """Analyse the document on a worker."""
        task = partial(analyse, str(folder), constants.MAX_PAGES)
        return await self._workers.run(constants.ANALYSE_TIMEOUT_S, task)


async def save_original(chunks: AsyncIterable[bytes], *, to: Path) -> None:
    """Stream the upload to `to`, refused as soon as it's too big or plainly not a PDF.

    Each chunk is written off the server's thread: a disk can be slow.
    """
    size, first_kb = 0, b""
    with to.open("wb") as out:
        async for chunk in chunks:
            size += len(chunk)
            if size > constants.MAX_FILE_BYTES:
                raise TooLarge(constants.MAX_FILE_MB)
            first_kb += chunk[: _HEADER_WINDOW - len(first_kb)]
            no_header = len(first_kb) == _HEADER_WINDOW and _PDF_HEADER not in first_kb
            if no_header:
                raise NotAPdf()
            await asyncio.to_thread(out.write, chunk)
    if _PDF_HEADER not in first_kb:
        raise NotAPdf()
