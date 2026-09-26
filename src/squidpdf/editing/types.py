"""Shapes editing shares between the log, the fit check and what render sends back.

TypedDicts for the JSON, as in `documents/types.py`; no framework, so the pool
can import them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

# How a too-long replacement is drawn; the names the user's options go by.
type Strategy = Literal["as-is", "shrink", "condense"]


@dataclass(frozen=True, slots=True)
class Skipped:
    """An edit left out because what it points at isn't in the document.

    `edit` is its position in the list the browser sent.
    """

    edit: int
    type: str
    detail: str


@dataclass(frozen=True, slots=True)
class Notice:
    """An edit that went in, but not quite as asked, and why in plain words.

    A replace is named by its span; an insert, which has none, by its place in
    the list the browser sent, as Skipped does.
    """

    span_id: str | None
    detail: str
    edit: int | None = None


@dataclass(frozen=True, slots=True)
class Applied:
    """What applying the log left out, and what it drew other than asked."""

    skipped: list[Skipped]
    notices: list[Notice]


@dataclass(frozen=True, slots=True)
class Region:
    """What to draw: a full-width strip of a page, from `y0` to `y1` in points, or all of it."""

    page: int
    y0: float | None = None
    y1: float | None = None


class ImageInfo(TypedDict):
    """A drawn strip, its top `y` in points on the page, as a base64 PNG."""

    page: int
    y: float
    image: str


class FitInfo(TypedDict):
    """Whether a replacement fits, the ways out if not, and which one was drawn."""

    delta_pt: float
    missing: list[str]
    left_out: list[str]
    options: list[Strategy]
    strategy: Strategy
    message: str | None


class InsertFitInfo(FitInfo):
    """An insert's fit, named by its place in the list the browser sent."""

    edit: int


class FaceInfo(TypedDict):
    """A face new text can be drawn in, and each letter it draws, by width.

    `name` is what an insert's `font` asks for. Widths are in thousandths of the
    font size, the same the server measures with, so a preview matches the draw.
    """

    name: str
    style: str  # "regular", "bold", "italic" or "bold-italic"
    glyphs: dict[str, float]


class FamilyInfo(TypedDict):
    """A family of faces we ship, and what kind of font it is."""

    family: str
    category: str  # "sans", "serif", "mono" or "handwriting"
    license: str
    same_widths_as: list[str]  # document fonts it stands in for without moving anything
    faces: list[FaceInfo]


class FontList(TypedDict):
    """Every face we ship, by family, under this build."""

    build: str
    families: list[FamilyInfo]


class Rendered(TypedDict):
    """What render worked out: the strips, a fit per replaced span, and what it skipped.

    `notices` are edits drawn other than asked, such as in a stand-in font.
    """

    images: list[ImageInfo]
    fits: dict[str, FitInfo]
    insert_fits: list[InsertFitInfo]
    redactions: list[dict[str, str]]
    skipped: list[Skipped]
    notices: list[Notice]


class Render(Rendered):
    """Render's reply, as the browser gets it."""

    build: str
    expires_at: str
