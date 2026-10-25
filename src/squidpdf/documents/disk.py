"""Writes that a full disk refuses in words: the server is full, not broken."""

from __future__ import annotations

import errno
from collections.abc import Iterator
from contextlib import contextmanager

from squidpdf.documents.errors import ServerFull


@contextmanager
def full_disk_refused() -> Iterator[None]:
    """Raise ServerFull for a write the disk has no room left for: full, not broken."""
    try:
        yield
    except OSError as exc:  # raised by a write; only a full disk is ours to name
        if exc.errno != errno.ENOSPC:
            raise
        raise ServerFull from exc
