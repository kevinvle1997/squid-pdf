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
import time
from pathlib import Path

import orjson

from squidpdf.core import Fragment, Page, Rect, Span, SpanIndex
from squidpdf.documents.constants import IDLE_S

ORIGINAL = "original.pdf"
_OWNER = "owner"
_INDEX = "index.json"
_PAGES = "pages.json"
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
    """Restart the idle clock; returns when the document now expires, as epoch seconds."""
    os.utime(folder)
    return folder.stat().st_mtime + IDLE_S


def delete(folder: Path) -> None:
    """The document and everything worked out from it."""
    shutil.rmtree(folder, ignore_errors=True)  # the sweeper may have got there first


def sweep() -> None:
    """Delete every document left untouched for longer than the idle hour."""
    cutoff = time.time() - IDLE_S
    for folder in root().glob("*"):
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            delete(folder)


def save_index(folder: Path, index: SpanIndex) -> None:
    """Keep the index, built once from the original, so ids never change."""
    (folder / _INDEX).write_bytes(orjson.dumps(list(index)))


def load_index(folder: Path) -> SpanIndex | None:
    """The saved index, or None before the first analysis."""
    try:
        raw = orjson.loads((folder / _INDEX).read_bytes())
    except FileNotFoundError:  # not analysed yet
        return None
    return SpanIndex(
        [
            Span(
                id=s["id"],
                page=s["page"],
                text=s["text"],
                font=s["font"],
                size=s["size"],
                color=tuple(s["color"]),
                bbox=Rect(**s["bbox"]),
                origin=tuple(s["origin"]),
                fragments=tuple(
                    Fragment(f["text"], Rect(**f["bbox"]), tuple(f["origin"]))
                    for f in s["fragments"]
                ),
            )
            for s in raw
        ]
    )


def save_pages(folder: Path, pages: list[Page]) -> None:
    """Keep the page list, read on every page view."""
    (folder / _PAGES).write_bytes(orjson.dumps(pages))


def load_pages(folder: Path) -> list[Page]:
    """The saved page list."""
    return [Page(**p) for p in orjson.loads((folder / _PAGES).read_bytes())]


def save_analysis(folder: Path, build: str, analysis: bytes) -> None:
    """Keep what was worked out under this build; another build works it out again."""
    (folder / f"analysis-{build}.json").write_bytes(analysis)


def load_analysis(folder: Path, build: str) -> bytes | None:
    """The analysis saved under this build, or None if it hasn't been worked out."""
    try:
        return (folder / f"analysis-{build}.json").read_bytes()
    except FileNotFoundError:  # a new build, or never analysed
        return None
