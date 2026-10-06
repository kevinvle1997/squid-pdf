"""Where documents live: one folder each, deleted whole.

A folder holds the original, its owner's hash, index, page list, the user's copies of its
fonts, and the analysis per `build`, tuning and set of copies. Each file worked out from the
original is named for its format. Its mtime, touched each visit, is the idle clock.
"""

from __future__ import annotations

import errno
import fcntl
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
    ANALYSIS_TUNING,
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
from squidpdf.documents import constants
from squidpdf.documents.errors import Gone, ServerFull
from squidpdf.documents.types import KeptAnalysis

ORIGINAL = "original.pdf"
_OWNER = "owner"
# Google's copies of fonts, cached beside the documents: no document id looks like it.
_GOOGLE_FONTS = "fonts"
# The user's own copies of a document's fonts, in its folder, each `<name's digest>.<digest>`:
# a name runs to 127 bytes, too long for a file's name in hex.
_ATTACHED = "fonts"
_ARRIVING = ".arriving-"  # a copy still coming in, or being checked: not attached yet
_ATTACHED_LOCK = ".fonts-lock"  # held by whoever attaches or removes a copy, and analyses
_ATTACHED_DIGEST_SIZE = 8  # bytes of each digest in a copy's file name, and of a set's
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
        owner_digest = (folder / _OWNER).read_text()
    except FileNotFoundError:  # unknown, or swept a moment ago
        return None
    # Its index is kept in another format: it reads as gone, so the browser opens it again.
    if not (folder / _index_file()).exists():
        return None
    return folder, owner_digest


@dataclass(frozen=True, slots=True, eq=False)
class AttachedFiles:
    """The user's own copies of a document's fonts, by the font's name: each read when asked.

    A render needs only the copies its fonts lend from, so none is read before then.
    """

    paths: dict[str, Path]  # each copy's file, by the digest of its font's name

    @property
    def key(self) -> str:
        """What tells this set of copies from another, for the analysis's file names."""
        names = "/".join(sorted(path.name for path in self.paths.values()))
        return hashlib.blake2s(names.encode(), digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()

    def __getitem__(self, font_name: str) -> bytes:
        """The copy for `font_name`. Raises KeyError if it was removed since it was listed."""
        try:
            return self.paths[_digest_of_name(font_name)].read_bytes()
        except FileNotFoundError as exc:  # removed or replaced since: no copy, not no document
            raise KeyError(font_name) from exc

    def __contains__(self, font_name: object) -> bool:
        """Whether there's a copy for `font_name`, read without reading it."""
        return isinstance(font_name, str) and _digest_of_name(font_name) in self.paths

    def __len__(self) -> int:
        """How many copies there are."""
        return len(self.paths)


def attached_files(folder: Path) -> AttachedFiles:
    """The user's own copies of the document's fonts, listed now, each read when asked for."""
    try:
        listed = list((folder / _ATTACHED).iterdir())
    except FileNotFoundError:  # none was ever attached, or the document was deleted
        return AttachedFiles({})
    # A dot first: one still arriving, not attached yet.
    kept = [path for path in listed if not path.name.startswith(".")]
    return AttachedFiles({_name_digest_in(path): path for path in kept})


def arriving_font(folder: Path) -> Path:
    """A new, empty file for a copy to come into, in the document's folder: it goes with it."""
    attached = folder / _ATTACHED
    with full_disk_refused():
        try:
            attached.mkdir(exist_ok=True)
            handle, arriving = tempfile.mkstemp(dir=attached, prefix=_ARRIVING)
        except FileNotFoundError as exc:  # the document was deleted since it was found
            raise Gone from exc
    os.close(handle)
    return Path(arriving)


def keep_font(folder: Path, font_name: str, arrived: Path) -> None:
    """Attach the copy that `arrived` for `font_name`, in place of any it had."""
    digest = hashlib.blake2s(arrived.read_bytes(), digest_size=_ATTACHED_DIGEST_SIZE)
    kept = folder / _ATTACHED / f"{_digest_of_name(font_name)}.{digest.hexdigest()}"
    try:
        os.replace(arrived, kept)
    except FileNotFoundError as exc:  # the document was deleted mid-way
        raise Gone from exc
    drop_font(folder, font_name, keeping=kept)


def drop_font(folder: Path, font_name: str, *, keeping: Path | None = None) -> None:
    """Remove the user's copy of `font_name`, all but `keeping`; nothing if there's none."""
    for name_digest, path in attached_files(folder).paths.items():
        if name_digest == _digest_of_name(font_name) and path != keeping:
            path.unlink(missing_ok=True)


@contextmanager
def fonts_locked(folder: Path) -> Iterator[None]:
    """Hold the document's copies still: one attach or removal at a time, across workers."""
    try:
        lock = (folder / _ATTACHED_LOCK).open("a")
    except FileNotFoundError as exc:  # the document was deleted since it was found
        raise Gone from exc
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released as the file closes
        yield


def _digest_of_name(font_name: str) -> str:
    """What a copy's file is named for: its font's name, at a length any name has."""
    return hashlib.blake2s(font_name.encode(), digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()


def _name_digest_in(path: Path) -> str:
    """The digest of the name of the font a kept copy is for, from its file's name."""
    name_digest, _digest = path.name.split(".")
    return name_digest


def open_to_analyse(folder: Path, attached: AttachedFiles) -> Engine:
    """The document's original, open to be judged. Raises Gone if it was deleted meanwhile.

    The one open that downloads Google's copies of its fonts, into the cache
    beside the documents (the sweep passes over it). Every other open reads
    that cache alone, so it lends what the analysis fetched and never waits.
    """
    google = google_fonts(folder=root() / _GOOGLE_FONTS)
    sources = FontSources(google=google, attached=attached)
    try:
        return open_pdf(str(folder / ORIGINAL), sources=sources)
    except FileNotFoundError as exc:  # deleted since it was found: by its owner or the sweep
        raise Gone from exc


def open_original(folder: Path) -> Engine:
    """The document's original, open for editing. Raises Gone if it was deleted meanwhile.

    Google's copy of a font lends the letters its copies in the file lack, from
    the cache the analysis filled: a render never waits on the network.
    """
    google = google_fonts(folder=root() / _GOOGLE_FONTS, cache_only=True)
    sources = FontSources(google=google, attached=attached_files(folder))
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
        return folder.stat().st_mtime + constants.IDLE_S
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
        return folder.stat().st_mtime < time.time() - constants.IDLE_S
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
    _write_whole(folder / _index_file(), index_as_json(index))


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


def require_index(folder: Path) -> SpanIndex:
    """The index saved at upload; raises Gone if it was deleted or kept in another format.

    Read once per worker: parsing a large one was most of a render. Kept while
    its file is the same file, so an index saved again or deleted is never served.
    """
    try:
        index_file = (folder / _index_file()).open("rb")
    except FileNotFoundError as exc:  # saved at upload: a delete, a sweep or a deploy took it
        raise Gone from exc
    with index_file:
        # Mtime and inode: each save is a new file, but the clock may not have moved.
        index_stat = os.fstat(index_file.fileno())
        file_identity = (folder, index_stat.st_mtime_ns, index_stat.st_ino)
        return _kept_index.index_for(file_identity, partial(_read_index, index_file))


def _read_index(index_file: BinaryIO) -> SpanIndex:
    """The index saved in `index_file`, already open, each span rebuilt."""
    return index_from_json(index_file.read())


def save_pages(folder: Path, pages: list[Page]) -> None:
    """Keep the page list, read on every page view."""
    _write_whole(folder / _pages_file(), pages_as_json(pages))


def load_pages(folder: Path) -> list[Page]:
    """The saved page list. Raises Gone if it was deleted, or kept in another format."""
    try:
        saved = (folder / _pages_file()).read_bytes()
    except FileNotFoundError as exc:  # a delete, a sweep or a deploy took it
        raise Gone from exc
    return pages_from_json(saved)


def save_analysis(folder: Path, build: str, kept: KeptAnalysis, *, attached: str) -> None:
    """Keep what was worked out under this build and set of copies (`AttachedFiles.key`).

    Another build, tuning or set of copies works it out again. The digest last: a
    304 reads it alone, so its file says the rest is there.
    """
    judged_with = f"{build}.{attached}"
    _write_whole(folder / _spans_file(judged_with), kept.spans)
    _write_whole(folder / _analysis_file(judged_with), kept.facts)
    _write_whole(folder / _digest_file(judged_with), kept.digest.encode())


def load_analysis(folder: Path, build: str) -> KeptAnalysis | None:
    """The analysis saved under this build, format and the copies attached now, or None."""
    judged_with = f"{build}.{attached_files(folder).key}"
    try:
        digest = (folder / _digest_file(judged_with)).read_text()
        facts = (folder / _analysis_file(judged_with)).read_bytes()
        spans = (folder / _spans_file(judged_with)).read_bytes()
    except FileNotFoundError:  # a new build, format or set of copies, or never analysed
        return None
    return KeptAnalysis(facts, spans, digest)


def load_analysis_digest(folder: Path, build: str) -> str | None:
    """The analysis's digest under this build, format and copies attached now, or None."""
    judged_with = f"{build}.{attached_files(folder).key}"
    try:
        return (folder / _digest_file(judged_with)).read_text()
    except FileNotFoundError:  # a new build, format or set of copies, or never analysed
        return None


def _index_file() -> str:
    """The file the span index is kept in."""
    return _kept_as("index.json")


def _pages_file() -> str:
    """The file the page list is kept in, beside `_index_file`."""
    return _kept_as("pages.json")


def _analysis_file(build: str) -> str:
    """The file the analysis under `build` is kept in."""
    return _analysis_kept_as(f"analysis-{build}.json")


def _spans_file(build: str) -> str:
    """The file the analysis's spans under `build` are kept in, beside `_analysis_file`."""
    return _analysis_kept_as(f"spans-{build}.json")


def _digest_file(build: str) -> str:
    """The file the analysis's digest under `build` is kept in, beside `_analysis_file`."""
    return _analysis_kept_as(f"digest-{build}.txt")


def _kept_as(name: str) -> str:
    """The file `name` is kept in, named for the format of a document's files.

    One in another format reads as not there, and an index is never rebuilt, so
    its document reads as gone.
    """
    return f"v{constants.DOCUMENT_FORMAT}.{name}"


def _analysis_kept_as(name: str) -> str:
    """The file `name` is kept in, named for the analysis's format and tuning as well.

    One in another format or tuning is worked out again over the same index. The
    document's format is in it too, so a gone document has no analysis to answer from.
    """
    tuning = repr(ANALYSIS_TUNING).encode()
    judged_by = hashlib.blake2s(tuning, digest_size=_TUNING_DIGEST_SIZE).hexdigest()
    return _kept_as(f"a{constants.ANALYSIS_FORMAT}.{judged_by}.{name}")
