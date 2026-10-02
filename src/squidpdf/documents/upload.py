"""Upload: the file streamed to disk, checked, kept and judged. No web framework here."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterable
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

from squidpdf.core import BUILD, Reply, Workers, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.errors import Gone, NotAPdf, ServerFull, TooLarge
from squidpdf.documents.replies import document_json
from squidpdf.documents.types import Analysis

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB


@dataclass(frozen=True, slots=True, eq=False)
class UploadController:
    """Upload, from request to reply."""

    workers: Workers  # where it analyses, off the server's own thread

    async def upload(
        self,
        owner_digest: str,
        *,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        said_in: str,
    ) -> Reply[bytes]:
        """Keep `chunks` as a new document for this owner, and answer with every span judged.

        `declared` is the size the request states, None when it streams without
        one. Refuses a file too large or not a PDF, and keeps nothing then. Refuses
        any file while the disk is nearly full.
        """
        # Read as module attributes, so a test can lower the limit.
        declared_too_large = declared is not None and declared > constants.MAX_FILE_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FILE_MB)
        if _disk_nearly_full():
            raise ServerFull()
        doc_id, folder = store.create(owner_digest)
        try:
            await _save_original(chunks, to=folder / store.ORIGINAL)
            await self._enqueue_analyse(folder)
            # Sent as kept, as a read sends it: writing it out again takes a while.
            kept = await asyncio.to_thread(store.load_analysis, folder, BUILD)
        except BaseException:  # refused, damaged, or the browser left: keep nothing
            await asyncio.to_thread(store.delete, folder)
            raise
        if kept is None:  # deleted since it was analysed
            raise Gone()
        body = document_json(doc_id, expires_at=store.touch(folder), kept=kept, said_in=said_in)
        return Reply(body, words.language_headers(said_in), HTTPStatus.CREATED)

    async def _enqueue_analyse(self, folder: Path) -> Analysis:
        """Analyse the document on a worker."""
        task = partial(analyse, str(folder), constants.MAX_PAGES)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _disk_nearly_full() -> bool:
    """Whether the disk documents are kept on has less than MIN_FREE_BYTES free."""
    root = store.root()
    root.mkdir(parents=True, exist_ok=True)  # otherwise made by the first upload
    # Read as a module attribute, so a test can raise the floor.
    return shutil.disk_usage(root).free < constants.MIN_FREE_BYTES


async def _save_original(chunks: AsyncIterable[bytes], *, to: Path) -> None:
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
