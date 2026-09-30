"""Where documents live: one folder each, deleted whole.

A folder holds the original, the owner's hash, the span index, the page list,
and the analysis for each `build`. Delete it and everything goes. Its mtime is
the idle clock: every visit touches it, and the sweeper deletes what's gone an
hour untouched.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import orjson

from squidpdf.core import LEVEL, SOLID, Fragment, Page, Rect, Span, SpanIndex
from squidpdf.documents.constants import IDLE_S
from squidpdf.documents.errors import Gone

__all__ = [
    "ORIGINAL",
    "root",
    "create",
    "find",
    "touch",
    "delete",
    "sweep",
    "save_index",
    "load_index",
    "save_pages",
    "load_pages",
    "save_analysis",
    "load_analysis",
]

ORIGINAL = "original.pdf"
_OWNER = "owner"
_INDEX = "index.json"
_PAGES = "pages.json"
_ANALYSIS_FORMAT = "codes"  # every sentence kept as its Message, said when sent
_ID_BYTES = 16
# What token_urlsafe(_ID_BYTES) makes; nothing else touches disk, so no id climbs out.
_ID_SHAPE = re.compile(r"[A-Za-z0-9_-]{22}")


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
    if not _ID_SHAPE.fullmatch(doc_id):
        return None
    folder = root() / doc_id
    try:
        return folder, (folder / _OWNER).read_text()
    except FileNotFoundError:  # unknown, or swept a moment ago
        return None


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
    """The document and everything worked out from it."""
    shutil.rmtree(folder, ignore_errors=True)  # the sweeper may have got there first


def sweep() -> None:
    """Delete every document left untouched for longer than the idle hour."""
    cutoff = time.time() - IDLE_S
    for folder in root().glob("*"):
        try:
            idle = folder.is_dir() and folder.stat().st_mtime < cutoff
        except FileNotFoundError:  # its owner deleted it since the listing
            continue
        if idle:
            delete(folder)


def write_whole(path: Path, data: bytes) -> None:
    """Write `data` to `path` in one step: a reader sees the old file or the new, never half.

    Written beside it, then renamed over it, which the filesystem does at once.
    A half file (a worker killed mid-write, a full disk) would read as broken
    JSON on every visit, and every visit restarts the hour, so it would never go.
    """
    handle, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(part, path)
    except BaseException:  # cut short: the old file stays, and the piece goes
        Path(part).unlink(missing_ok=True)
        raise


def save_index(folder: Path, index: SpanIndex) -> None:
    """Keep the index, built once from the original, so ids never change."""
    write_whole(folder / _INDEX, orjson.dumps(list(index)))


def load_index(folder: Path) -> SpanIndex | None:
    """The saved index, or None before the first analysis."""
    try:
        raw = orjson.loads((folder / _INDEX).read_bytes())
    except FileNotFoundError:  # not analysed yet
        return None
    return SpanIndex([load_span(span) for span in raw])


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
    except FileNotFoundError as exc:  # upload saves it first, so only a sweep removes it
        raise Gone from exc
    return [Page(**page) for page in saved]


def save_analysis(folder: Path, build: str, analysis: bytes) -> None:
    """Keep what was worked out under this build; another build works it out again."""
    write_whole(folder / analysis_file(build), analysis)


def load_analysis(folder: Path, build: str) -> bytes | None:
    """The analysis saved under this build, or None if it hasn't been worked out."""
    try:
        return (folder / analysis_file(build)).read_bytes()
    except FileNotFoundError:  # a new build, or never analysed
        return None


def analysis_file(build: str) -> str:
    """The file the analysis under `build` is kept in.

    Named for how it's kept too: one kept before its sentences were codes
    reads as not worked out yet, and is worked out again over the same index.
    """
    return f"analysis-{build}.{_ANALYSIS_FORMAT}.json"
