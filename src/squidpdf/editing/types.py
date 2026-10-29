"""Shapes editing shares between the log, the fit reports and what render sends back.

TypedDicts for the JSON, as in `documents/types.py`; no framework, so the pool
can import them. A name ending in Info is JSON the browser gets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from squidpdf.core import (
    Category,
    Fidelity,
    HiddenPlace,
    Message,
    MessageInfo,
    Page,
    SaidInfo,
    Style,
)
from squidpdf.documents.types import ApproximateInfo
from squidpdf.editing.edits import Strategy
from squidpdf.editing.fit import FitReport, Verdict

# Why an edit was left out: what it points at isn't in the document.
type _SkipReason = Literal["bad_reference"]


@dataclass(frozen=True, slots=True)
class Skipped:
    """An edit left out because what it points at isn't in the document.

    `edit` is its position in the list the browser sent.
    """

    edit: int
    type: _SkipReason
    detail: Message


@dataclass(frozen=True, slots=True)
class SpanNotice:
    """A replace or a redaction that went in, but not quite as asked: its span, and why."""

    span_id: str
    detail: Message


@dataclass(frozen=True, slots=True)
class InsertNotice:
    """An insert that went in, but not quite as asked, and why.

    It has no span, so it's named by its place in the list the browser sent, as Skipped is.
    """

    edit: int
    detail: Message


@dataclass(frozen=True, slots=True)
class FileNotice:
    """What saving did to the whole file other than asked, such as drop its tags, and why."""

    detail: Message


# What came out other than asked: about a span, an insert, or the whole file.
Notice = SpanNotice | InsertNotice | FileNotice


@dataclass(frozen=True, slots=True)
class Replaced:
    """A replace as drawn, and the name of the font it replaced the words of."""

    verdict: Verdict
    shown_name: str  # a file's words: logged only through loggable_family


@dataclass(frozen=True, slots=True)
class Applied:
    """What applying the log left out, what it drew other than asked, and how replaces did."""

    skipped: list[Skipped]
    notices: list[Notice]
    replaced: list[Replaced]


@dataclass(frozen=True, slots=True)
class EditedText:
    """The text the edits on one page take out, and the text they draw in its place or anew."""

    erased: list[str]
    drawn: list[str]


@dataclass(frozen=True, slots=True)
class Redaction:
    """A redaction drawn: whether its words are gone, where else they were, and why not.

    `hidden_copies` are the places read from the document as it came, then "signatures"
    if signed.
    """

    verified: bool
    hidden_copies: list[HiddenPlace]
    covered: bool  # its words are left only in its box, under new text sharing one


@dataclass(frozen=True, slots=True)
class Region:
    """What to draw: a full-width strip of a page, from `y0` to `y1` in points, or all of it."""

    page: int
    y0: float | None = None
    y1: float | None = None


@dataclass(frozen=True, slots=True)
class DrawnPage:
    """A page render draws on, and its pixels per point, its page image's."""

    page: Page
    scale: float


@dataclass(frozen=True, slots=True)
class Rendered:
    """What render worked out, in no one's words yet: the strips, the fits, and the skips.

    `notices` are edits drawn other than asked, such as in a substitute.
    `redactions` are the redactions drawn, by span id.
    """

    images: list[ImageInfo] = field(repr=False)  # each a PNG, in base64
    fits: dict[str, FitReport]  # by the replaced span's id
    insert_fits: dict[int, FitReport]  # by the insert's place in the list the browser sent
    verdicts: dict[str, Verdict]  # how each replace came out, by its span's id, as `fits`
    insert_verdicts: dict[int, Verdict]  # how each insert came out, as `insert_fits`
    skipped: list[Skipped]
    notices: list[Notice]
    redactions: dict[str, Redaction]


@dataclass(frozen=True, slots=True)
class Exported:
    """What export came out with besides the file: the edits it left out and the file's notices.

    `skipped_edits` are places in the browser's edit list. `file_notices` are
    about the whole file, so they name no span.
    """

    skipped_edits: list[int]
    file_notices: list[Message]


@dataclass(frozen=True, slots=True)
class Saved:
    """What saving an edited file came out with.

    `applied` is about the edits; `notices` about the file as a whole, such as dropped tags.
    """

    applied: Applied
    notices: list[Message]


class ImageInfo(TypedDict):
    """A drawn strip, its top `y` in points on the page, as a base64 PNG."""

    page: int
    y: float
    image: str


class FitInfo(TypedDict):
    """Whether a replacement fits, the ways out if not, which one was drawn, and its verdict.

    The verdict is the edit's own, as drawn, in the four fields a span's at upload is in
    (`SpanInfo`): `why` set only when approximate, `substitute` only when substitute.
    """

    delta_pt: float
    missing: list[str]
    left_out: list[str]
    options: list[Strategy]
    strategy: Strategy
    message: str | None  # every part below in the reader's words, in one line
    message_parts: list[MessageInfo]  # what's wrong, each for the browser to say itself
    fidelity: Fidelity
    why: ApproximateInfo | None
    substitute: str | None  # the face we ship that draws it, when its font can't
    same_widths: bool  # that face's letters are as wide as the original's


class InsertFitInfo(FitInfo):
    """An insert's fit, named by its place in the list the browser sent."""

    edit: int


class RedactionInfo(TypedDict):
    """A redaction drawn: whether its words are gone from the file, and where else they were.

    `hidden_copies` ends with "signatures" in a signed file: any redaction takes them.
    `message` says it in the reader's words; None when there's nothing to say.
    """

    verified: bool
    hidden_copies: list[HiddenPlace]
    message: str | None


class SkippedInfo(MessageInfo):
    """An edit left out, by its place in the list the browser sent, and why.

    `detail` is why in the reader's words; `code` and `params` the same, unsaid.
    """

    edit: int
    type: str
    detail: str


class SpanNoticeInfo(SaidInfo):
    """A replace or a redaction drawn other than asked, by its span, and why."""

    kind: Literal["span"]
    span_id: str


class InsertNoticeInfo(SaidInfo):
    """An insert drawn other than asked, by its place in the list the browser sent, and why."""

    kind: Literal["insert"]
    edit: int


class FileNoticeInfo(SaidInfo):
    """What saving did to the whole file other than asked, and why."""

    kind: Literal["file"]


# A notice as the browser gets it, told apart by `kind`. A plain alias: pydantic reads it.
NoticeInfo = SpanNoticeInfo | InsertNoticeInfo | FileNoticeInfo


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

    `notices` are edits drawn other than asked, such as in a substitute.
    `redactions` are the redactions drawn, by span id, as `fits` are.
    """

    images: list[ImageInfo]
    fits: dict[str, FitInfo]
    insert_fits: list[InsertFitInfo]
    redactions: dict[str, RedactionInfo]
    skipped: list[SkippedInfo]
    notices: list[NoticeInfo]
    build: str
    expires_at: str
