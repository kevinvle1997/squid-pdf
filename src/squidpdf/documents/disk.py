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
    held_bytes: int = 0

    @contextmanager
    def holding(self, size: int, *, free: int) -> Iterator[None]:
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
        self.held_bytes = 0


_uploads_under_way = _UploadsUnderWay()


@asynccontextmanager
async def disk_held(declared: int | None, *, most: int) -> AsyncIterator[None]:
    free = await asyncio.to_thread(_documents_disk_free)
    with _uploads_under_way.holding(most if declared is None else declared, free=free):
        yield


def _documents_disk_free() -> int:
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(folder).free


def root() -> Path:
    return Path(os.environ.get("SQUIDPDF_DATA", "data")).resolve()


@contextmanager
def full_disk_refused() -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        if exc.errno != errno.ENOSPC:
            raise
        raise ServerFull from exc
