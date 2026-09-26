"""Shapes editing shares between the log, the fit reports and what render sends back.

TypedDicts for the JSON, as in `documents/types.py`; no framework, so the pool
can import them. A name ending in Info is JSON the browser gets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict

from squidpdf.core.message import Message, MessageInfo
from squidpdf.core.types import Category, Style

if TYPE_CHECKING:  # fit.py imports this module for Strategy
    from squidpdf.editing.fit import LogFits

# How a too-long replacement is drawn; the names the user's options go by.
type Strategy = Literal["as-is", "shrink", "condense"]


@dataclass(frozen=True, slots=True)
class Skipped:
    """An edit left out because what it points at isn't in the document.

    `edit` is its position in the list the browser sent.
    """

    edit: int
    type: str
    detail: Message


@dataclass(frozen=True, slots=True)
class Notice:
    """An edit that went in, but not quite as asked, and why.

    A replace is named by its span; an insert, which has none, by its place in
    the list the browser sent, as Skipped does.
    """

    span_id: str | None
    detail: Message
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


@dataclass(frozen=True, slots=True)
class Rendered:
    """What render worked out, in no one's words yet: the strips, the fits, and the skips.

    `notices` are edits drawn other than asked, such as in a stand-in font.
    """

    images: list[ImageInfo]
    fits: LogFits
    skipped: list[Skipped]
    notices: list[Notice]


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
    message: str | None  # every part below in the reader's words, in one line
    message_parts: list[MessageInfo]  # what's wrong, each for the browser to say itself


class InsertFitInfo(FitInfo):
    """An insert's fit, named by its place in the list the browser sent."""

    edit: int


class SkippedInfo(MessageInfo):
    """An edit left out, by its place in the list the browser sent, and why.

    `detail` is why in the reader's words; `code` and `params` the same, unsaid.
    """

    edit: int
    type: str
    detail: str


class NoticeInfo(MessageInfo):
    """An edit drawn other than asked, by its span or its place in the list, and why.

    `detail` is why in the reader's words; `code` and `params` the same, unsaid.
    """

    span_id: str | None
    detail: str
    edit: int | None


class FaceInfo(TypedDict):
    """A face new text can be drawn in, and each letter it draws, by width.

    `name` is what an insert's `font` asks for. Widths are in thousandths of the
    font size, the same the server measures with, so a preview matches the draw.
    """

    name: str
    style: Style
    glyphs: dict[str, float]


class FamilyInfo(TypedDict):
    """A family of faces we ship, and what kind of font it is."""

    family: str
    category: Category
    license: str
    same_widths_as: list[str]  # document fonts it stands in for without moving anything
    faces: list[FaceInfo]


class FontList(TypedDict):
    """Every face we ship, by family, under this build."""

    build: str
    families: list[FamilyInfo]


class Render(TypedDict):
    """Render's reply, as the browser gets it: the strips, a fit per edit, and what it skipped.

    `notices` are edits drawn other than asked, such as in a stand-in font.
    """

    images: list[ImageInfo]
    fits: dict[str, FitInfo]
    insert_fits: list[InsertFitInfo]
    redactions: list[dict[str, str]]
    skipped: list[SkippedInfo]
    notices: list[NoticeInfo]
    build: str
    expires_at: str
