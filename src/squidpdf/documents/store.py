"""Where documents live: one folder each, deleted whole.

A folder holds the original, the owner's hash, the span index, the page list, and the analysis
for each `build` and tuning, in files of their own. Delete it and everything goes. Its mtime is
the idle clock: every visit touches it, and the sweeper deletes what's gone an hour untouched.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import BinaryIO

from squidpdf.core import (
    FIDELITY_TUNING,
    Engine,
    FontSources,
    Page,
    SpanIndex,
    google_fonts,
    index_as_json,
    index_from_json,
    open_pdf,
    pages_as_json,
    pages_from_json,
)
from squidpdf.documents.constants import IDLE_S
from squidpdf.documents.errors import Gone, ServerFull
from squidpdf.documents.types import KeptAnalysis

ORIGINAL = "original.pdf"
_OWNER = "owner"
_INDEX = "index.json"
# Renamed with `turn_cw`: a document kept before it reads as gone, and the browser
# uploads it again.
_PAGES = "pages.turn_cw.json"
# Google's copies of fonts, cached beside the documents: no document id looks like it.
_GOOGLE_FONTS = "fonts"
# How a kept analysis is written: one kept in an older way is worked out again.
# "codes": every sentence kept as its Message, said when sent; "turn_cw": each
# page's turn named with its way; "form-fields": each span says whether a form
# field draws it.
_ANALYSIS_FORMAT = "codes.turn_cw.form-fields"
# Bytes of the tuning's digest in file names: enough that two tunings won't share one.
_TUNING_DIGEST_SIZE = 8
_ID_BYTES = 16
# What token_urlsafe(_ID_BYTES) makes; nothing else touches disk, so no id climbs out.
_DOCUMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}")
_TRASH = ".trash-"  # a deleted document's folder, moved aside while it's emptied
_TRASHED_FOLDER_PATTERN = re.compile(re.escape(_TRASH) + _DOCUMENT_ID_PATTERN.pattern)


def root() -> Path:
    """The folder every document lives under: `SQUIDPDF_DATA`, or ./data."""
    return Path(os.environ.get("SQUIDPDF_DATA", "data")).resolve()  # unset in development


def create(owner_digest: str) -> tuple[str, Path]:
    """A new, empty document folder that answers to this owner; none is left if it fails."""
    # One disk on one box is the only copy: at a second box, this moves to S3.
    doc_id = secrets.token_urlsafe(_ID_BYTES)
    folder = root() / doc_id
    folder.mkdir(parents=True)
    try:
        (folder / _OWNER).write_text(owner_digest)
    except BaseException:  # a full disk, say: no reply names it, only the sweep clears it
        delete(folder)
        raise
    return doc_id, folder


def find(doc_id: str) -> tuple[Path, str] | None:
    """The document's folder and its owner's hash, or None if there's no such document."""
    if not _DOCUMENT_ID_PATTERN.fullmatch(doc_id):
        return None
    folder = root() / doc_id
    try:
        return folder, (folder / _OWNER).read_text()
    except FileNotFoundError:  # unknown, or swept a moment ago
        return None


def open_to_analyse(folder: Path) -> Engine:
    """The document's original, open to be judged. Raises Gone if it was deleted meanwhile.

    The one open that downloads Google's copies of its fonts, into the cache
    beside the documents (the sweep passes over it). Every other open reads
    that cache alone, so it lends what the analysis fetched and never waits.
    """
    sources = FontSources(google=google_fonts(folder=root() / _GOOGLE_FONTS))
    try:
        return open_pdf(str(folder / ORIGINAL), sources=sources)
    except FileNotFoundError as exc:  # deleted since it was found: by its owner or the sweep
        raise Gone from exc


def open_original(folder: Path) -> Engine:
    """The document's original, open for editing. Raises Gone if it was deleted meanwhile.

    Google's copy of a font lends the letters its copies in the file lack, from
    the cache the analysis filled: a render never waits on the network.
    """
    sources = FontSources(google=google_fonts(folder=root() / _GOOGLE_FONTS, cache_only=True))
    try:
        return open_pdf(str(folder / ORIGINAL), sources=sources)
    except FileNotFoundError as exc:  # deleted since it was found: by its owner or the sweep
        raise Gone from exc


def touch(folder: Path) -> float:
    """Restart the idle clock; returns when the document now expires, as epoch seconds.

    Raises Gone if it was deleted since it was found, by its owner or the sweeper.
    """
    try:
        os.utime(folder)
        return folder.stat().st_mtime + IDLE_S
    except FileNotFoundError as exc:  # deleted between find() and here
        raise Gone from exc


def delete(folder: Path) -> None:
    """The document and everything worked out from it, gone for every request at once.

    Moved aside first, in one step, so its id stops resolving before any file
    goes: a worker still writing finds it Gone, instead of adding a file to a
    folder being emptied, which would stop the folder going.
    """
    trash = folder.with_name(f"{_TRASH}{folder.name}")
    try:
        folder.rename(trash)
    except FileNotFoundError:  # the sweep or another delete got there first
        return
    # The folder and everything in it. Errors ignored: the sweep may be emptying it too.
    shutil.rmtree(trash, ignore_errors=True)
    # Once more: a writer that found the folder just before the move can still drop one
    # piece in it after the first pass listed it, which stops the folder going.
    # Its next write finds the document Gone.
    shutil.rmtree(trash, ignore_errors=True)


def sweep() -> None:
    """Delete every document left untouched for longer than the idle hour, and nothing else.

    Only a folder named like a document, or one a delete moved aside: the data
    folder can be shared. A request that loses the race with a delete says Gone.
    """
    for folder in root().glob("*"):
        # A delete cut short: nothing reads it now.
        if _TRASHED_FOLDER_PATTERN.fullmatch(folder.name):
            shutil.rmtree(folder, ignore_errors=True)  # its delete may be emptying it too
            continue
        # With or without its owner: one emptied by a delete from before they moved aside.
        is_document = _DOCUMENT_ID_PATTERN.fullmatch(folder.name) and folder.is_dir()
        if is_document and _idle(folder):
            delete(folder)


def _idle(folder: Path) -> bool:
    """Whether the folder has gone untouched past the idle hour; False once it's gone."""
    try:
        return folder.stat().st_mtime < time.time() - IDLE_S
    except FileNotFoundError:  # its owner deleted it since the listing
        return False


@contextmanager
def full_disk_refused() -> Iterator[None]:
    """Raise ServerFull for a write the disk has no room left for: full, not broken."""
    try:
        yield
    except OSError as exc:  # raised by a write; only a full disk is ours to name
        if exc.errno != errno.ENOSPC:
            raise
        raise ServerFull from exc


def _write_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` in one step: a reader sees the old file or the new, never half.

    Written beside it, then renamed over it, which the filesystem does at once. A half file
    would read as broken JSON, and each visit restarts the hour, so it would never go.
    Raises Gone if the document was deleted meanwhile, and ServerFull if the disk is full.
    """
    with full_disk_refused():
        try:
            handle, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        except FileNotFoundError as exc:  # its folder was deleted since it was found
            raise Gone from exc
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(part, path)
        except FileNotFoundError as exc:  # its folder was deleted mid-write
            Path(part).unlink(missing_ok=True)  # most likely gone with the folder already
            raise Gone from exc
        except BaseException:  # cut short: the old file stays, and the piece goes
            Path(part).unlink(missing_ok=True)
            raise


def save_index(folder: Path, index: SpanIndex) -> None:
    """Keep the index, built once from the original, so ids never change."""
    _write_whole(folder / _INDEX, index_as_json(index))


@dataclass(slots=True)
class _KeptIndex:
    """The last index this worker read, and which file it read it from."""

    file_identity: tuple[Path, int, int] | None = None  # folder, mtime and inode
    index: SpanIndex | None = field(default=None, repr=False)  # every span in the document

    def index_for(
        self, file_identity: tuple[Path, int, int], read: Callable[[], SpanIndex]
    ) -> SpanIndex:
        """The index in the file `file_identity` names: the one kept, else read and kept."""
        kept = self.index if file_identity == self.file_identity else None
        # The same file as last time: it isn't read again.
        if kept is not None:
            return kept
        # Dropped before the next is read, so two never share the worker's memory cap.
        self.forget()
        index = read()
        self.keep(file_identity, index)
        return index

    def keep(self, file_identity: tuple[Path, int, int], index: SpanIndex) -> None:
        """Keep `index`, read from the file `file_identity` names."""
        self.file_identity, self.index = file_identity, index

    def forget(self) -> None:
        """Drop the index kept, as a fresh record: the next one is read from its file."""
        self.file_identity, self.index = None, None


_kept_index = _KeptIndex()  # per worker process: each has its own


def load_index(folder: Path) -> SpanIndex | None:
    """The saved index, or None before the first analysis.

    Read once per worker: parsing a large one was most of a render. Kept while
    its file is the same file, so an index saved again or deleted is never served.
    """
    try:
        index_file = (folder / _INDEX).open("rb")
    except FileNotFoundError:  # not analysed yet, or deleted since
        return None
    with index_file:
        # Mtime and inode: each save is a new file, but the clock may not have moved.
        index_stat = os.fstat(index_file.fileno())
        file_identity = (folder, index_stat.st_mtime_ns, index_stat.st_ino)
        return _kept_index.index_for(file_identity, partial(_read_index, index_file))


def _read_index(index_file: BinaryIO) -> SpanIndex:
    """The index saved in `index_file`, already open, each span rebuilt."""
    return index_from_json(index_file.read())


def require_index(folder: Path) -> SpanIndex:
    """The saved index of a document analysed at upload. Raises Gone if it was deleted since.

    For work after upload, where a missing index can only mean a delete; analysis
    reads `load_index`'s None as "not analysed yet".
    """
    index = load_index(folder)
    if index is None:  # analysed at upload, so a sweep or a delete removed it
        raise Gone
    return index


def save_pages(folder: Path, pages: list[Page]) -> None:
    """Keep the page list, read on every page view."""
    _write_whole(folder / _PAGES, pages_as_json(pages))


def load_pages(folder: Path) -> list[Page]:
    """The saved page list. Raises Gone if the sweep deleted the document meanwhile."""
    try:
        saved = (folder / _PAGES).read_bytes()
    except FileNotFoundError as exc:  # upload saves it first: a sweep or a delete removed it
        raise Gone from exc
    return pages_from_json(saved)


def save_analysis(folder: Path, build: str, kept: KeptAnalysis) -> None:
    """Keep what was worked out under this build; another build or tuning works it out again.

    The digest last: a 304 reads it alone, so its file says the rest is there.
    """
    _write_whole(folder / _spans_file(build), kept.spans)
    _write_whole(folder / _analysis_file(build), kept.facts)
    _write_whole(folder / _digest_file(build), kept.digest.encode())


def load_analysis(folder: Path, build: str) -> KeptAnalysis | None:
    """The analysis saved under this build, or None if it hasn't been worked out."""
    try:
        digest = (folder / _digest_file(build)).read_text()
        facts = (folder / _analysis_file(build)).read_bytes()
        spans = (folder / _spans_file(build)).read_bytes()
    except FileNotFoundError:  # a new build, or never analysed
        return None
    return KeptAnalysis(facts, spans, digest)


def load_analysis_digest(folder: Path, build: str) -> str | None:
    """The saved analysis's digest under this build, or None if it hasn't been worked out."""
    try:
        return (folder / _digest_file(build)).read_text()
    except FileNotFoundError:  # a new build, or never analysed
        return None


def _analysis_file(build: str) -> str:
    """The file the analysis under `build` is kept in.

    Named by `_kept_as` too, so one kept another way or under other tuning is worked out again.
    """
    return f"analysis-{build}.{_kept_as()}.json"


def _spans_file(build: str) -> str:
    """The file the analysis's spans under `build` are kept in, beside `_analysis_file`."""
    return f"spans-{build}.{_kept_as()}.json"


def _digest_file(build: str) -> str:
    """The file the analysis's digest under `build` is kept in, beside `_analysis_file`."""
    return f"digest-{build}.{_kept_as()}.txt"


def _kept_as() -> str:
    """How an analysis is kept (`_ANALYSIS_FORMAT`) and a digest of what tuning judged it."""
    tuning = repr(FIDELITY_TUNING).encode()
    judged_by = hashlib.blake2s(tuning, digest_size=_TUNING_DIGEST_SIZE).hexdigest()
    return f"{_ANALYSIS_FORMAT}.{judged_by}"
