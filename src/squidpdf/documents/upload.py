"""Upload: the file streamed to disk, checked, kept and judged. No web framework here."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import BinaryIO

from squidpdf.core import Reply, Workers, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse_upload
from squidpdf.documents.errors import NotAPdf, ServerFull, TooLarge
from squidpdf.documents.replies import document_json
from squidpdf.documents.types import KeptAnalysis

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB


@dataclass(slots=True)
class _UploadsUnderWay:
    """The disk the uploads under way may yet take, held against the free-disk floor."""

    held_bytes: int = 0  # what each upload under way may yet write

    @contextmanager
    def holding(self, size: int, *, free: int) -> Iterator[None]:
        """Hold `size` bytes of disk while the block runs, refused if `free` has no room for it.

        Checked and held in one step on the event loop: two uploads never pass on one room.
        """
        room = free - self.held_bytes - size
        # Read as a module attribute, so a test can raise the floor.
        if room < constants.MIN_FREE_BYTES:
            raise ServerFull()
        self.held_bytes += size
        try:
            yield
        finally:
            self.held_bytes -= size

    def forget(self) -> None:
        """Forget every upload held, as a fresh record: only with none under way."""
        self.held_bytes = 0


# This server's uploads under way: its one process takes every upload.
_uploads_under_way = _UploadsUnderWay()


@asynccontextmanager
async def disk_held(declared: int | None, *, most: int) -> AsyncIterator[None]:
    """Hold the disk an upload may yet write, against the floor, while the block runs.

    That's its `declared` size, which the server reads no more than, or `most` when
    it streams without one. Raises ServerFull if the floor has no room for it.
    """
    free = await asyncio.to_thread(_documents_disk_free)
    with _uploads_under_way.holding(most if declared is None else declared, free=free):
        yield


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
        any file while the disk is nearly full, or if it fills up anyway.
        """
        # Read as module attributes, so a test can lower the limit.
        declared_too_large = declared is not None and declared > constants.MAX_FILE_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FILE_MB)
        async with disk_held(declared, most=constants.MAX_FILE_BYTES):
            with store.full_disk_refused():
                doc_id, folder = await asyncio.to_thread(store.create, owner_digest)
                await _deleted_if_it_fails(
                    folder, _save_original(chunks, to=folder / store.ORIGINAL)
                )
        kept = await _deleted_if_it_fails(folder, self._enqueue_analyse(folder))
        # Deleted if it fails too: a browser that leaves first never gets the id.
        # Off the server's thread: it reads every font's letters and writes them out again.
        body = await _deleted_if_it_fails(
            folder,
            asyncio.to_thread(_reply_body, doc_id, folder=folder, kept=kept, said_in=said_in),
        )
        return Reply(body, words.language_headers(said_in), HTTPStatus.CREATED)

    async def _enqueue_analyse(self, folder: Path) -> KeptAnalysis:
        """Analyse the document on a worker."""
        task = partial(analyse_upload, str(folder), constants.MAX_PAGES)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _reply_body(doc_id: str, *, folder: Path, kept: KeptAnalysis, said_in: str) -> bytes:
    """The reply's JSON: the new document's hour started, every span judged as kept."""
    return document_json(doc_id, expires_at=store.touch(folder), kept=kept, said_in=said_in)


def _documents_disk_free() -> int:
    """The bytes free on the disk documents are kept on."""
    root = store.root()
    root.mkdir(parents=True, exist_ok=True)  # otherwise made by the first upload
    return shutil.disk_usage(root).free


async def _deleted_if_it_fails[T](folder: Path, step: Awaitable[T]) -> T:
    """What `step` gives; if it fails, the document in `folder` is deleted first."""
    try:
        return await step
    except BaseException:  # refused, damaged, or the browser left: keep nothing
        await asyncio.to_thread(store.delete, folder)
        raise


async def _save_original(chunks: AsyncIterable[bytes], *, to: Path) -> None:
    """Stream the upload to `to`, refused as soon as it's too big or plainly not a PDF.

    Opened, written and closed off the server's thread: a disk can be slow.
    """
    size, first_kb = 0, b""
    out = await asyncio.to_thread(_opened_to_write, to)
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


def _opened_to_write(path: Path) -> BinaryIO:
    """`path`, opened to be written from its start."""
    return path.open("wb")
