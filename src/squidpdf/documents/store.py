"""Where documents live: one folder each, deleted whole.

A folder holds the original, the owner's hash, the span index, the page list,
and the analysis for each `build`, its spans in a file of their own. Delete it
and everything goes. Its mtime is the idle clock: every visit touches it, and
the sweeper deletes what's gone an hour untouched.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from squidpdf.core import (
    LEVEL,
    SOLID,
    Engine,
    FontSources,
    Fragment,
    Page,
    Rect,
    Span,
    SpanIndex,
    google_fonts,
    index_of,
    open_pdf,
)
from squidpdf.documents.constants import IDLE_S
from squidpdf.documents.errors import Gone
from squidpdf.documents.types import KeptAnalysis

__all__ = [
    "ORIGINAL",
    "open_to_analyse",
    "open_original",
    "root",
    "create",
    "find",
    "touch",
    "delete",
    "sweep",
    "save_index",
    "load_index",
    "require_index",
    "save_pages",
    "load_pages",
    "save_analysis",
    "load_analysis",
    "analysis_file",
    "spans_file",
]

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
_ID_BYTES = 16
# What token_urlsafe(_ID_BYTES) makes; nothing else touches disk, so no id climbs out.
_DOCUMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{22}")
_TRASH = ".trash-"  # a deleted document's folder, moved aside while it's emptied
_TRASHED_FOLDER_PATTERN = re.compile(re.escape(_TRASH) + _DOCUMENT_ID_PATTERN.pattern)


def root() -> Path:
    """The folder every document lives under: `SQUIDPDF_DATA`, or ./data."""
    return Path(os.environ.get("SQUIDPDF_DATA", "data")).resolve()  # unset in development


def create(owner_digest: str) -> tuple[str, Path]:
    """A new, empty document folder that answers to this owner."""
    # One disk on one box is the only copy: at a second box, this moves to S3.
    doc_id = secrets.token_urlsafe(_ID_BYTES)
    folder = root() / doc_id
    folder.mkdir(parents=True)
    (folder / _OWNER).write_text(owner_digest)
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
        if is_document and idle(folder):
            delete(folder)


def idle(folder: Path) -> bool:
    """Whether the folder has gone untouched past the idle hour; False once it's gone."""
    try:
        return folder.stat().st_mtime < time.time() - IDLE_S
    except FileNotFoundError:  # its owner deleted it since the listing
        return False


def write_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` in one step: a reader sees the old file or the new, never half.

    Written beside it, then renamed over it, which the filesystem does at once.
    A half file (a worker killed mid-write, a full disk) would read as broken
    JSON on every visit, and every visit restarts the hour, so it would never go.
    Raises Gone if the document was deleted meanwhile, by its owner or the sweep.
    """
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
    write_whole(folder / _INDEX, orjson.dumps(list(index)))


@dataclass(slots=True)
class KeptIndex:
    """The last index this worker read, and which file it read it from."""

    file_identity: tuple[Path, int, int] | None = None  # folder, mtime and inode
    index: SpanIndex | None = None


kept_index = KeptIndex()  # per worker process: each has its own


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
        if file_identity == kept_index.file_identity:
            return kept_index.index
        # Dropped before the next is read, so two never share the worker's memory cap.
        kept_index.file_identity, kept_index.index = None, None
        raw = orjson.loads(index_file.read())
    kept_index.index = index_of(load_span(span) for span in raw)
    kept_index.file_identity = file_identity
    return kept_index.index


def require_index(folder: Path) -> SpanIndex:
    """The saved index of a document analysed at upload. Raises Gone if it was deleted since.

    For work after upload, where a missing index can only mean a delete; analysis
    reads `load_index`'s None as "not analysed yet".
    """
    index = load_index(folder)
    if index is None:  # analysed at upload, so a sweep or a delete removed it
        raise Gone
    return index


def load_span(saved: dict[str, Any]) -> Span:
    """One saved span. The keys are its fields; only the nested shapes need rebuilding."""
    rebuilt: dict[str, Any] = {
        "color": tuple(saved["color"]),
        # .get: an index saved before spans kept opacity; they were drawn solid then.
        "opacity": saved.get("opacity", SOLID),
        "bbox": Rect(**saved["bbox"]),
        "origin": tuple(saved["origin"]),
        "fragments": tuple(load_fragment(fragment) for fragment in saved["fragments"]),
        # .get: an index saved before spans kept their direction; all were drawn level then.
        "direction": tuple(saved.get("direction", LEVEL)),
    }
    return Span(**saved | rebuilt)


def load_fragment(saved: dict[str, Any]) -> Fragment:
    """One saved fragment, the same way."""
    rebuilt: dict[str, Any] = {"bbox": Rect(**saved["bbox"]), "origin": tuple(saved["origin"])}
    return Fragment(**saved | rebuilt)


def save_pages(folder: Path, pages: list[Page]) -> None:
    """Keep the page list, read on every page view."""
    write_whole(folder / _PAGES, orjson.dumps(pages))


def load_pages(folder: Path) -> list[Page]:
    """The saved page list. Raises Gone if the sweep deleted the document meanwhile."""
    try:
        saved = orjson.loads((folder / _PAGES).read_bytes())
    except FileNotFoundError as exc:  # upload saves it first: a sweep or a delete removed it
        raise Gone from exc
    return [Page(**page) for page in saved]


def save_analysis(folder: Path, build: str, kept: KeptAnalysis) -> None:
    """Keep what was worked out under this build; another build works it out again.

    The spans first: the facts' file is what says the analysis is there.
    """
    write_whole(folder / spans_file(build), kept.spans)
    write_whole(folder / analysis_file(build), kept.facts)


def load_analysis(folder: Path, build: str) -> KeptAnalysis | None:
    """The analysis saved under this build, or None if it hasn't been worked out."""
    try:
        facts = (folder / analysis_file(build)).read_bytes()
        spans = (folder / spans_file(build)).read_bytes()
    except FileNotFoundError:  # a new build, or never analysed
        return None
    return KeptAnalysis(facts, spans)


def analysis_file(build: str) -> str:
    """The file the analysis under `build` is kept in.

    Named for how it's kept too: one kept in an older way (`_ANALYSIS_FORMAT`)
    reads as not worked out yet, and is worked out again over the same index.
    """
    return f"analysis-{build}.{_ANALYSIS_FORMAT}.json"


def spans_file(build: str) -> str:
    """The file the analysis's spans under `build` are kept in, beside `analysis_file`."""
    return f"spans-{build}.{_ANALYSIS_FORMAT}.json"
