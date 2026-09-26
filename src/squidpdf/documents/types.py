"""The shapes a document takes: what's worked out from it, and what the browser gets.

TypedDicts for the JSON, so the analysis is typed where it's built and the
OpenAPI schema describes it; no framework, so the pool can import them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


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

    Widths are in thousandths of the font size.
    """

    name: str
    substitute: str | None
    why: str | None  # why the file's own copy can't be used, in plain words
    glyphs: dict[str, float]


class Analysis(TypedDict):
    """Everything worked out from the original under one build."""

    build: str
    pages: list[PageInfo]
    spans: list[SpanInfo]
    fonts: list[FontInfo]


class FitRules(TypedDict):
    """The thresholds the browser runs the fit check with, the server's own."""

    tolerance_pt: float
    condense_limit: float
    shrink_floor: float


class Copy(TypedDict):
    """The sentences the browser fills in as the user types."""

    missing: str
    too_long: str
    options: dict[str, dict[str, str]]


class Document(Analysis):
    """The stored document, as the browser gets it."""

    id: str
    expires_at: str
    fit: FitRules
    copy: Copy
    notices: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class Loaded:
    """A document this browser owns, with its hour just restarted."""

    id: str
    folder: Path
    expires_at: float
