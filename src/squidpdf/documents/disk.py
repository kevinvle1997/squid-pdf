"""The disk documents are kept on: where, what uploads under way hold, and a write refused.

A write it has no room for is the server being full, not broken.
"""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from squidpdf.documents import constants
from squidpdf.documents.errors import ServerFull


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


def _documents_disk_free() -> int:
    """The bytes free on the disk documents are kept on."""
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)  # otherwise made by the first upload
    return shutil.disk_usage(folder).free


def root() -> Path:
    """The folder every document lives under: `SQUIDPDF_DATA`, or ./data."""
    return Path(os.environ.get("SQUIDPDF_DATA", "data")).resolve()  # unset in development


@contextmanager
def full_disk_refused() -> Iterator[None]:
    """Raise ServerFull for a write the disk has no room left for: full, not broken."""
    try:
        yield
    except OSError as exc:  # raised by a write; only a full disk is ours to name
        if exc.errno != errno.ENOSPC:
            raise
        raise ServerFull from exc
