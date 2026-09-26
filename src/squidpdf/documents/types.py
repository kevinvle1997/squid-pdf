"""The shapes a document takes: what's worked out from it, and what the browser gets.

TypedDicts for the JSON, so the analysis is typed where it's built and the
OpenAPI schema describes it; no framework, so the pool can import them. A name
ending in Info is JSON the browser gets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from squidpdf.core.message import MessageInfo


class Box(TypedDict):
    """A box on the page in points, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float


class PageInfo(TypedDict):
    """A page unrotated, and the turn the browser gives it."""

    width: float
    height: float
    rotation: int


class SpanInfo(TypedDict):
    """One editable span and whether it keeps its own font."""

    id: str
    page: int
    text: str
    font: str
    size: float
    color: list[float]
    bbox: Box
    origin: list[float]
    fidelity: str


class FontInfo(TypedDict):
    """A font the spans use: what stands in for it, and each letter it really draws, by width.

    Widths are in thousandths of the font size, from the face that draws: the
    file's own copy, or the substitute when that can't be used.
    """

    name: str
    substitute: str | None  # the face we ship that draws it instead, e.g. "Carlito Bold"
    why: str | None  # why the file's own copy can't be used, in plain words
    same_widths: bool  # the substitute's letters are as wide as the original's
    glyphs: dict[str, float]


class Analysis(TypedDict):
    """Everything worked out from the original under one build."""

    build: str
    pages: list[PageInfo]
    spans: list[SpanInfo]
    # The document's own fonts. The faces we ship, which inserts can use too, are at /api/fonts.
    fonts: list[FontInfo]


class FitRules(TypedDict):
    """The thresholds the browser runs the fit check with, the server's own."""

    tolerance_pt: float
    condense_limit: float
    shrink_floor: float


class Copy(TypedDict):
    """The sentences the browser fills in as the user types, in the reader's language."""

    missing: str
    too_long: str
    stand_in: str  # when the substitute's letters may be another width
    stand_in_same_widths: str  # when they are exactly as wide: `same_widths` on the font
    undo_redaction: str
    options: dict[str, dict[str, str]]


class DocumentNoticeInfo(MessageInfo):
    """Something about the document that may not be what the user expected.

    `type` is what the browser branches on; `detail` says it in the reader's
    words, and `code` and `params` are the same, unsaid.
    """

    type: str
    detail: str


class Document(Analysis):
    """The stored document, as the browser gets it."""

    id: str
    expires_at: str
    fit: FitRules
    copy: Copy
    notices: list[DocumentNoticeInfo]


@dataclass(frozen=True, slots=True)
class Loaded:
    """A document this browser owns, with its hour just restarted."""

    id: str
    folder: Path
    expires_at: float
