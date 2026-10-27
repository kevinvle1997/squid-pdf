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
    font: str  # its font's name: the one a font in `fonts` goes by, where it has no object
    font_object: int | None  # its font's PDF object, as a font in `fonts` has; None if unlisted
    size: float
    color: list[float]
    bbox: Box
    origin: list[float]
    fidelity: Fidelity
    why: ApproximateInfo | None
    # Its own, not its font's: one font can be stored on one page and only named on another.
    substitute: str | None  # the face we ship that draws an edit here, when its font can't
    same_widths: bool  # that face's letters are as wide as the original's
    in_file: bool  # the file's own copy of its font can draw it, if only some of its letters
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

    # Its PDF object, which its spans name it by; None for one its page doesn't list.
    font_object: int | None
    name: str  # its own, subset prefix aside: what a copy of it is attached by
    # The name the reader knows it by: a Type3's copies share one, its own often its object's.
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

    font_object: int | None  # its PDF object, which its spans name it by; None if unlisted
    name: str  # what a copy of it is attached by, and a span of a font unlisted names it by
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
    signed: bool  # the original holds a signature, which any download from here breaks
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


class CountedSentence(TypedDict):
    """A sentence said with a count, a form for each plural category: the browser picks one."""

    one: str
    other: str


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
    page_left_out: str  # a page deleted in the editor, said on the slip in its place
    page_put_back: str  # a deleted page back in the download
    form_field_not_edited: str  # said of a span `form_field` marks, before any edit
    empty: str  # said while the text typed into a span is empty
    edit_breaks_signature: str  # said as the first edit of a signed document starts
    # Said to a screen reader as a span's words change, filled with its words now.
    back_to: str  # undone
    changed_to: str  # typed and kept, or done again
    put_back: str  # put back as the document had it, from its margin note
    redacted: str
    your_copy_in_use: str  # the user's copy of a font, by its shown name, taken in
    your_copy_removed: str
    warning_muted: str  # a font's warning muted, by its shown name
    warning_unmuted: str
    downloaded: str  # a file the browser downloaded, by its name
    # Find's count, and what Change all did.
    find_nothing: str
    find_identical: CountedSentence
    find_different: CountedSentence
    # When some places will look different: then find_some_different.
    find_places: CountedSentence
    find_some_different: CountedSentence  # shown as a warning
    changed_places: CountedSentence
    # The bar's count of the changes: all of them warned, or some, as Find's is.
    changes: CountedSentence
    changes_different: CountedSentence  # shown as a warning
    changes_similar: CountedSentence  # shown as a warning
    # When some changes won't match: then changes_some_different or changes_some_similar.
    changes_some: CountedSentence
    changes_some_different: CountedSentence  # shown as a warning
    changes_some_similar: CountedSentence  # shown as a warning
    # The bar's count before any change, shown as a warning.
    at_rest_different: CountedSentence
    at_rest_similar: CountedSentence
    changes_too_long: CountedSentence  # shown as a warning, after any change
    # Heard as typing comes to a verdict, where its full warning isn't.
    typed_identical: str
    typed_different: str
    typed_similar: str
    join_parts: str  # what joins sentences said at once
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
    signed: bool  # the original holds a signature: the first edit says it breaks it

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
