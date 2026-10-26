"""The shapes a document takes: what's worked out from it, and what the browser gets.

TypedDicts for the JSON, so the analysis is typed where it's built and the
OpenAPI schema describes it; no framework, so the pool can import them. A name
ending in Info is JSON the browser gets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from squidpdf.core import ApproximateReason, Fidelity, MessageInfo, Param, QuarterTurn


class Box(TypedDict):
    """A box on the page in points, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float


class PageInfo(TypedDict):
    """A page unrotated, and the turn the browser gives it: clockwise, as the file asks."""

    width: float
    height: float
    turn_cw: QuarterTurn


class ApproximateInfo(TypedDict):
    """How an approximate span would come back unlike itself, in no language.

    `code` names the reason, and its sentence: `sentences.approximate[code]`. `params` fill it.
    """

    code: ApproximateReason
    params: dict[str, Param]


class SpanInfo(TypedDict):
    """One editable span and whether it keeps its own font.

    `why` names how an approximate span comes back unlike itself (`sentences.approximate`);
    `form_field`, text a form field draws, which an edit leaves alone (`form_field_not_edited`).
    """

    id: str
    page: int
    text: str
    font: str
    size: float
    color: list[float]
    bbox: Box
    origin: list[float]
    fidelity: Fidelity
    why: ApproximateInfo | None
    # Its own, not its font's: one font can be stored on one page and only named on another.
    substitute: str | None  # the face we ship that draws an edit here, when its font can't
    same_widths: bool  # that face's letters are as wide as the original's
    letter_spacing_em: (
        float  # how far past its width each letter is set apart, per em of its size
    )
    room_pt: float  # the free space after it on its line: too long is only past it
    form_field: bool


class FontFacts(TypedDict):
    """A font the spans use, as the analysis keeps it: in no language, so any can say it.

    What stands in for it, and each letter it really draws, by width. Widths are
    in thousandths of the font size, from the face that draws: the file's own
    copy, or the substitute when that can't be used.
    """

    name: str
    # The name the reader knows it by: a Type3 font's copies share one, its text names none.
    shown_name: str
    substitute: str | None  # the face we ship that draws it instead, e.g. "Carlito Bold"
    why: MessageInfo | None  # why the file's own copy can't be used
    same_widths: bool  # the substitute's letters are as wide as the original's
    look_alike: str  # the face we ship that draws a line with a letter no copy of it has
    attached: bool  # the user attached their own copy of it
    glyphs: dict[str, float]


class FontInfo(TypedDict):
    """A font the spans use, as the browser gets it: its facts, and why in the reader's words.

    `why_code` and `why_params` are `why` unsaid, both empty when `why` is None.
    """

    name: str
    shown_name: str  # the name the reader knows it by; copies of one font share it
    substitute: str | None
    why: str | None  # why the file's own copy can't be used, in the reader's words
    why_code: str | None
    why_params: dict[str, Param]
    same_widths: bool
    look_alike: str  # what the browser previews an edit in, and names when a letter is missing
    attached: bool  # the user attached their own copy of it, which they can remove
    glyphs: dict[str, float]


class AnalysisFacts(TypedDict):
    """The analysis of the original under one build, less its spans: read on every visit."""

    build: str
    active_content_dropped: bool  # the original had what acts on its own taken out
    pages: list[PageInfo]
    # The document's own fonts. The faces we ship, which inserts can use too, are at /api/fonts.
    fonts: list[FontFacts]


@dataclass(frozen=True, slots=True)
class KeptAnalysis:
    """An analysis as kept: the facts, read when sent, the spans, sent as is, and their digest.

    Files apart, so a read needn't find the spans inside the rest: on a long
    document they're nearly all of it, and parsing them held up the server.
    The digest is worked out when kept, so a 304 reads neither.
    """

    facts: bytes = field(repr=False)  # AnalysisFacts, as JSON
    spans: bytes = field(repr=False)  # list[SpanInfo], as JSON
    digest: str  # of the facts and the spans


class FitRules(TypedDict):
    """The thresholds the browser runs the fit check with, the server's own."""

    tolerance_pt: float
    room_slack_pt: float
    condense_limit: float
    shrink_floor: float


class Sentences(TypedDict):
    """The sentences the browser says about the document, unfilled, in the reader's language."""

    missing: str
    will_leave_out: (
        str  # letters no font we have draws, said before the edit as the render says them after
    )
    too_long: str
    substitute: str  # when the substitute's letters may be another width
    substitute_same_widths: str  # when they are exactly as wide: `same_widths` on the font
    drawn_in_your_copy: str  # a font the user attached their own copy of
    drawn_in_own_copy: str  # a font whose copy in the file draws edits
    undo_redaction: str
    reopened: str
    export_left_out: str
    form_field_not_edited: str  # said of a span `form_field` marks, before any edit
    empty: str  # said while the text typed into a span is empty
    options: dict[str, dict[str, str]]
    # Each way a span can be approximate, by its `why` code.
    approximate: dict[ApproximateReason, str]
    # Each character the catalog names, by itself: how one that draws nothing alone is said.
    characters: dict[str, str]


class DocumentNoticeInfo(MessageInfo):
    """Something about the document that may not be what the user expected.

    `type` is what the browser branches on; `detail` says it in the reader's
    words, and `code` and `params` are the same, unsaid.
    """

    type: str
    detail: str


class Document(TypedDict):
    """The stored document, as the browser gets it, in the reader's language."""

    build: str
    pages: list[PageInfo]
    spans: list[SpanInfo]
    fonts: list[FontInfo]

    id: str
    expires_at: str
    fit: FitRules
    sentences: Sentences
    notices: list[DocumentNoticeInfo]


@dataclass(frozen=True, slots=True)
class Loaded:
    """A document this browser owns, with its hour just restarted."""

    id: str
    folder: Path
    expires_at: float
