"""The folder's mtime, touched each visit, is the idle clock."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, partial
from pathlib import Path
from typing import BinaryIO, Protocol

from squidpdf.core import (
    ANALYSIS_TUNING,
    DEFAULT_DRIVER_FACTORY,
    DriverFactory,
    Engine,
    FontSources,
    FreeSpace,
    GoogleDownload,
    Page,
    SpanIndex,
    free_space_as_json,
    free_space_from_json,
    google_fonts,
    index_as_json,
    index_from_json,
    open_pdf,
    pages_as_json,
    pages_from_json,
)
from squidpdf.documents import constants
from squidpdf.documents.attached import AttachedFiles, attached_files
from squidpdf.documents.disk import full_disk_refused, root
from squidpdf.documents.errors import Gone
from squidpdf.documents.types import KeptAnalysis

ORIGINAL = "original.pdf"
_OWNER = "owner"
_ACTIVE_CONTENT_DROPPED = "active-content-dropped"
_PLAIN_XMP_DROPPED = "plain-xmp-dropped"
# Google's copies of fonts, cached beside the documents: no document id looks like it.
_GOOGLE_FONTS = "fonts"
_TUNING_DIGEST_SIZE = 8
# Set in development: an analysis is also keyed by the app's source, so a change re-judges.
_JUDGE_BY_SOURCE = "SQUIDPDF_DEV_REJUDGE"
_SOURCE_FILES = ("*.py", "*.toml")
_ID_BYTES = 16
# What token_urlsafe(_ID_BYTES) makes; nothing else touches disk, so no id climbs out.
_DOCUMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}")
_TRASH = ".trash-"
_TRASHED_FOLDER_PATTERN = re.compile(re.escape(_TRASH) + _DOCUMENT_ID_PATTERN.pattern)


class DocumentStore(Protocol):
    """It crosses to the workers with each job, so it pickles."""

    @property
    def driver_factory(self) -> DriverFactory: ...

    def create(self, owner_digest: str) -> tuple[str, Path]: ...

    def find(self, doc_id: str) -> tuple[Path, str] | None: ...

    def open_to_write(self, folder: Path) -> BinaryIO: ...

    def open_to_analyse(self, folder: Path, attached: AttachedFiles) -> Engine: ...

    def open_original(self, folder: Path) -> Engine: ...

    def touch(self, folder: Path) -> float: ...

    def delete(self, folder: Path) -> None: ...

    def sweep(self) -> None: ...


@dataclass(frozen=True, slots=True)
class FolderStore:
    driver_factory: DriverFactory
    google_download: GoogleDownload | None = None

    def create(self, owner_digest: str) -> tuple[str, Path]:
        doc_id = secrets.token_urlsafe(_ID_BYTES)
        folder = root() / doc_id
        folder.mkdir(parents=True)
        try:
            (folder / _OWNER).write_text(owner_digest)
        except BaseException:  # a full disk, say: no reply names it, only the sweep clears it
            self.delete(folder)
            raise
        return doc_id, folder

    def find(self, doc_id: str) -> tuple[Path, str] | None:
        if not _DOCUMENT_ID_PATTERN.fullmatch(doc_id):
            return None
        folder = root() / doc_id
        try:
            owner_digest = (folder / _OWNER).read_text()
        except FileNotFoundError:
            return None
        # Its index is kept in another format: it reads as gone, so the browser opens it again.
        if not (folder / _index_file()).exists():
            return None
        return folder, owner_digest

    def open_to_write(self, folder: Path) -> BinaryIO:
        return (folder / ORIGINAL).open("wb")

    def open_to_analyse(self, folder: Path, attached: AttachedFiles) -> Engine:
        """The one open that downloads Google's fonts; every other reads its cache alone."""
        google = google_fonts(folder=root() / _GOOGLE_FONTS, download=self.google_download)
        sources = FontSources(google=google, attached=attached)
        try:
            return open_pdf(
                str(folder / ORIGINAL), sources=sources, driver_factory=self.driver_factory
            )
        except FileNotFoundError as exc:
            raise Gone from exc

    def open_original(self, folder: Path) -> Engine:
        google = google_fonts(
            folder=root() / _GOOGLE_FONTS, cache_only=True, download=self.google_download
        )
        sources = FontSources(google=google, attached=attached_files(folder))
        try:
            return open_pdf(
                str(folder / ORIGINAL), sources=sources, driver_factory=self.driver_factory
            )
        except FileNotFoundError as exc:
            raise Gone from exc

    def touch(self, folder: Path) -> float:
        try:
            os.utime(folder)
            return folder.stat().st_mtime + constants.IDLE_S
        except FileNotFoundError as exc:
            raise Gone from exc

    def delete(self, folder: Path) -> None:
        """Moved aside first, so its id stops resolving before any file goes."""
        trash = folder.with_name(f"{_TRASH}{folder.name}")
        try:
            folder.rename(trash)
        except FileNotFoundError:
            return
        shutil.rmtree(trash, ignore_errors=True)
        # Once more: a writer that found the folder before the move can still drop a piece in it
        # after the first pass, which stops the folder going; its next write finds it Gone.
        shutil.rmtree(trash, ignore_errors=True)

    def sweep(self) -> None:
        """Only a document's folder, or one a delete moved aside: the data folder is shared."""
        for folder in root().glob("*"):
            if _TRASHED_FOLDER_PATTERN.fullmatch(folder.name):
                shutil.rmtree(folder, ignore_errors=True)
                continue
            is_document = _DOCUMENT_ID_PATTERN.fullmatch(folder.name) and folder.is_dir()
            if is_document and _idle(folder):
                self.delete(folder)


FOLDER_STORE = FolderStore(DEFAULT_DRIVER_FACTORY)


def _idle(folder: Path) -> bool:
    try:
        return folder.stat().st_mtime < time.time() - constants.IDLE_S
    except FileNotFoundError:
        return False


def replace_original(folder: Path, write: Callable[[str], object]) -> None:
    _replace_whole(folder / ORIGINAL, lambda part: write(str(part)))


def keep_active_content_dropped(folder: Path) -> None:
    _write_whole(folder / _ACTIVE_CONTENT_DROPPED, b"")


def active_content_dropped(folder: Path) -> bool:
    return (folder / _ACTIVE_CONTENT_DROPPED).exists()


def keep_plain_xmp_dropped(folder: Path) -> None:
    _write_whole(folder / _PLAIN_XMP_DROPPED, b"")


def plain_xmp_dropped(folder: Path) -> bool:
    return (folder / _PLAIN_XMP_DROPPED).exists()


def _write_whole(path: Path, data: bytes) -> None:
    """A half file reads as broken JSON, and each visit restarts the hour: it'd never go."""
    _replace_whole(path, lambda part: part.write_bytes(data))


def _replace_whole(path: Path, write: Callable[[Path], object]) -> None:
    with full_disk_refused():
        try:
            handle, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        except FileNotFoundError as exc:
            raise Gone from exc
        os.close(handle)
        try:
            write(Path(part))
            os.replace(part, path)
        except FileNotFoundError as exc:
            Path(part).unlink(missing_ok=True)
            raise Gone from exc
        except BaseException:
            Path(part).unlink(missing_ok=True)
            raise


def save_index(folder: Path, index: SpanIndex) -> None:
    index_path = folder / _index_file()
    _write_whole(index_path, index_as_json(index))
    try:
        index_stat = index_path.stat()
    except FileNotFoundError as exc:
        raise Gone from exc
    _kept_indexes.keep(_IndexFile.from_stat(folder, index_stat), index)


@dataclass(frozen=True, slots=True)
class _IndexFile:
    folder: Path
    # Mtime and inode: each save is a new file, but the clock may not have moved.
    mtime_ns: int
    inode: int
    size: int

    @classmethod
    def from_stat(cls, folder: Path, index_stat: os.stat_result) -> _IndexFile:
        return cls(folder, index_stat.st_mtime_ns, index_stat.st_ino, index_stat.st_size)


@dataclass(frozen=True, slots=True)
class _KeptIndex:
    index_file: _IndexFile
    index: SpanIndex = field(repr=False)


@dataclass(slots=True)
class _KeptIndexes:
    # Each by its document's folder, the least recently used first.
    by_folder: dict[Path, _KeptIndex] = field(default_factory=dict, repr=False)

    def index_for(self, index_file: _IndexFile, *, read: Callable[[], SpanIndex]) -> SpanIndex:
        kept = self.by_folder.pop(index_file.folder, None)
        if kept is not None and kept.index_file == index_file:
            self.by_folder[index_file.folder] = kept
            return kept.index
        self._make_space_for(index_file.size)
        index = read()
        self.keep(index_file, index)
        return index

    def keep(self, index_file: _IndexFile, index: SpanIndex) -> None:
        self._make_space_for(index_file.size)
        self.by_folder[index_file.folder] = _KeptIndex(index_file, index)

    def _make_space_for(self, file_bytes: int) -> None:
        kept_bytes = sum(kept.index_file.size for kept in self.by_folder.values())
        while self.by_folder and kept_bytes + file_bytes > constants.KEPT_INDEX_BYTES:
            least_used = self.by_folder.pop(next(iter(self.by_folder)))
            kept_bytes -= least_used.index_file.size

    def forget(self) -> None:
        self.by_folder.clear()


_kept_indexes = _KeptIndexes()


def require_index(folder: Path) -> SpanIndex:
    try:
        opened = (folder / _index_file()).open("rb")
    except FileNotFoundError as exc:
        raise Gone from exc
    with opened:
        index_file = _IndexFile.from_stat(folder, os.fstat(opened.fileno()))
        return _kept_indexes.index_for(index_file, read=partial(_read_index, opened))


def _read_index(opened: BinaryIO) -> SpanIndex:
    return index_from_json(opened.read())


def save_pages(folder: Path, pages: list[Page]) -> None:
    _write_whole(folder / _pages_file(), pages_as_json(pages))


def load_pages(folder: Path) -> list[Page]:
    try:
        saved = (folder / _pages_file()).read_bytes()
    except FileNotFoundError as exc:
        raise Gone from exc
    return pages_from_json(saved)


def save_free_space(folder: Path, build: str, free_space: FreeSpace) -> None:
    _write_whole(folder / _free_space_file(build), free_space_as_json(free_space))


def load_free_space(folder: Path, build: str) -> FreeSpace | None:
    try:
        saved = (folder / _free_space_file(build)).read_bytes()
    except FileNotFoundError:
        return None
    return free_space_from_json(saved)


def save_analysis(folder: Path, build: str, kept: KeptAnalysis, *, attached: str) -> None:
    """The digest last: a 304 reads it alone, so its file says the rest is there."""
    judged_with = f"{build}.{attached}"
    _write_whole(folder / _spans_file(judged_with), kept.spans)
    _write_whole(folder / _analysis_file(judged_with), kept.facts)
    _write_whole(folder / _digest_file(judged_with), kept.digest.encode())


def load_analysis(folder: Path, build: str) -> KeptAnalysis | None:
    judged_with = f"{build}.{attached_files(folder).key}"
    try:
        digest = (folder / _digest_file(judged_with)).read_text()
        facts = (folder / _analysis_file(judged_with)).read_bytes()
        spans = (folder / _spans_file(judged_with)).read_bytes()
    except FileNotFoundError:
        return None
    return KeptAnalysis(facts, spans, digest)


def load_analysis_digest(folder: Path, build: str) -> str | None:
    judged_with = f"{build}.{attached_files(folder).key}"
    try:
        return (folder / _digest_file(judged_with)).read_text()
    except FileNotFoundError:
        return None


def _index_file() -> str:
    return _kept_as("index.json")


def _pages_file() -> str:
    return _kept_as("pages.json")


@cache
def _source_digest() -> bytes:
    package = Path(__file__).resolve().parents[1]
    files = sorted(path for pattern in _SOURCE_FILES for path in package.rglob(pattern))
    digest = hashlib.blake2s()
    for path in files:
        digest.update(path.read_bytes())
    return digest.digest()


def _free_space_file(build: str) -> str:
    return _analysis_kept_as(f"free-space-{build}.json")


def _analysis_file(build: str) -> str:
    return _analysis_kept_as(f"analysis-{build}.json")


def _spans_file(build: str) -> str:
    return _analysis_kept_as(f"spans-{build}.json")


def _digest_file(build: str) -> str:
    return _analysis_kept_as(f"digest-{build}.txt")


def _kept_as(name: str) -> str:
    return f"v{constants.DOCUMENT_FORMAT}.{name}"


def _analysis_kept_as(name: str) -> str:
    tuning = repr(ANALYSIS_TUNING).encode()
    # In development, the code that judged it: a server reloaded on a change is a new process.
    source = _source_digest() if os.environ.get(_JUDGE_BY_SOURCE) else b""
    judged_by = hashlib.blake2s(tuning + source, digest_size=_TUNING_DIGEST_SIZE).hexdigest()
    return _kept_as(f"a{constants.ANALYSIS_FORMAT}.{judged_by}.{name}")
