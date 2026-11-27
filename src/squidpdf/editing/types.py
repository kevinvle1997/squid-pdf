"""No framework, so the pool can import them. A name ending in Info is JSON the browser gets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from squidpdf.core import (
    Category,
    DrawnLine,
    Fidelity,
    HiddenPlace,
    Message,
    MessageInfo,
    Page,
    PlacedLetter,
    Rect,
    RedactedRun,
    RestOfLine,
    SaidInfo,
    Span,
    Style,
)
from squidpdf.documents.types import ApproximateInfo, Box
from squidpdf.editing.edits import Strategy
from squidpdf.editing.fit import FitReport, Verdict

type _SkipReason = Literal["bad_reference"]


@dataclass(frozen=True, slots=True)
class Skipped:
    position: int
    type: _SkipReason
    detail: Message


@dataclass(frozen=True, slots=True)
class SpanNotice:
    span_id: str
    detail: Message


@dataclass(frozen=True, slots=True)
class InsertNotice:
    position: int
    detail: Message


@dataclass(frozen=True, slots=True)
class FileNotice:
    detail: Message


Notice = SpanNotice | InsertNotice | FileNotice


@dataclass(frozen=True, slots=True)
class Replaced:
    verdict: Verdict
    shown_name: str  # a file's words: logged only through loggable_family


@dataclass(frozen=True, slots=True)
class Applied:
    skipped: list[Skipped]
    notices: list[Notice]
    replaced: list[Replaced]


@dataclass(frozen=True, slots=True)
class Redacted:
    span: Span
    runs: tuple[RedactedRun, ...]
    ranges: tuple[range, ...]
    rest: tuple[PlacedLetter, ...] | None


@dataclass(frozen=True, slots=True)
class EditedPage:
    erased: list[Rect]
    drawn: list[DrawnLine]


@dataclass(frozen=True, slots=True)
class Redaction:
    verified: bool
    hidden_copies: list[HiddenPlace]
    covered: bool
    moved: bool
    unchecked: bool
    rest: RestOfLine
    words: str
    page: int
    runs: list[tuple[range, Rect]]


@dataclass(frozen=True, slots=True)
class Region:
    page: int
    y0: float | None = None
    y1: float | None = None


@dataclass(frozen=True, slots=True)
class DrawnPage:
    page: Page
    scale: float


@dataclass(frozen=True, slots=True)
class Rendered:
    images: list[ImageInfo] = field(repr=False)
    fits: dict[str, FitReport]
    insert_fits: dict[int, FitReport]
    verdicts: dict[str, Verdict]
    insert_verdicts: dict[int, Verdict]
    skipped: list[Skipped]
    notices: list[Notice]
    redactions: dict[str, Redaction]


@dataclass(frozen=True, slots=True)
class Exported:
    skipped_edits: list[int]
    file_notices: list[Message]


@dataclass(frozen=True, slots=True)
class Saved:
    applied: Applied
    notices: list[Message]


class ImageInfo(TypedDict):
    page: int
    y: float
    image: str


class FitInfo(TypedDict):
    delta_pt: float
    missing: list[str]
    left_out: list[str]
    options: list[Strategy]
    strategy: Strategy
    message: str | None
    message_parts: list[MessageInfo]
    fidelity: Fidelity
    why: ApproximateInfo | None
    substitute: str | None
    same_widths: bool


class InsertFitInfo(FitInfo):
    edit: int


class RedactedRunInfo(TypedDict):
    range: list[int]
    box: Box


class RedactionInfo(TypedDict):
    verified: bool
    hidden_copies: list[HiddenPlace]
    message: str | None
    runs: list[RedactedRunInfo]


class SkippedInfo(MessageInfo):
    edit: int
    type: str
    detail: str


class SpanNoticeInfo(SaidInfo):
    kind: Literal["span"]
    span_id: str


class InsertNoticeInfo(SaidInfo):
    kind: Literal["insert"]
    edit: int


class FileNoticeInfo(SaidInfo):
    kind: Literal["file"]


# A notice as the browser gets it, told apart by `kind`. A plain alias: pydantic reads it.
NoticeInfo = SpanNoticeInfo | InsertNoticeInfo | FileNoticeInfo


class FaceInfo(TypedDict):
    """Widths in thousandths of the size, as the server measures: a preview matches the draw."""

    name: str
    style: Style
    glyphs: dict[str, float]


class FamilyInfo(TypedDict):
    family: str
    category: Category
    license: str
    same_widths_as: list[str]
    faces: list[FaceInfo]


class FontList(TypedDict):
    build: str
    families: list[FamilyInfo]


class Render(TypedDict):
    images: list[ImageInfo]
    fits: dict[str, FitInfo]
    insert_fits: list[InsertFitInfo]
    redactions: dict[str, RedactionInfo]
    skipped: list[SkippedInfo]
    notices: list[NoticeInfo]
    build: str
    expires_at: str
