from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from squidpdf.core import (
    ApproximateReason,
    Fidelity,
    FreeSpace,
    MessageInfo,
    Param,
    QuarterTurn,
)


class Box(TypedDict):
    x0: float
    y0: float
    x1: float
    y1: float


class PageInfo(TypedDict):
    width: float
    height: float
    turn_cw: QuarterTurn


class ApproximateInfo(TypedDict):
    code: ApproximateReason
    params: dict[str, Param]


class SpanInfo(TypedDict):
    id: str
    page: int
    text: str
    font: str
    font_object: int | None
    size: float
    color: list[float]
    bbox: Box
    origin: list[float]
    fidelity: Fidelity
    why: ApproximateInfo | None
    # Its own, not its font's: one font can be stored on one page and only named on another.
    substitute: str | None
    same_widths: bool
    in_file: bool
    letter_spacing_em: float
    room_pt: float
    form_field: bool
    turned: bool


class LineInfo(TypedDict):
    span_ids: list[str]
    room_pt: float


class FontFacts(TypedDict):
    """Widths are in thousandths of the font size, from the face that draws."""

    font_object: int | None
    name: str
    shown_name: str
    substitute: str | None
    why: MessageInfo | None
    same_widths: bool
    look_alike: str
    attached: bool
    glyphs: dict[str, float]


class FontInfo(TypedDict):
    font_object: int | None
    name: str
    shown_name: str
    substitute: str | None
    why: str | None
    why_code: str | None
    why_params: dict[str, Param]
    same_widths: bool
    look_alike: str
    attached: bool
    glyphs: dict[str, float]


class AnalysisFacts(TypedDict):
    build: str
    active_content_dropped: bool
    plain_xmp_dropped: bool
    signed: bool
    pages: list[PageInfo]
    fonts: list[FontFacts]
    lines: list[LineInfo]


@dataclass(frozen=True, slots=True)
class KeptAnalysis:
    """Files apart, so a read needn't parse the spans: on a long document they're most of it."""

    facts: bytes = field(repr=False)
    spans: bytes = field(repr=False)
    digest: str


@dataclass(frozen=True, slots=True)
class JudgedPages:
    """Some of a document's pages judged: one worker's share of its analysis, not kept yet."""

    spans: bytes = field(repr=False)  # list[SpanInfo] of the spans on its pages, as JSON
    # Each font first met on its pages, by the first span in it there, in index order.
    fonts: list[FontFacts] = field(repr=False)
    lines: list[LineInfo] = field(repr=False)  # each of more than one span on its pages
    # Its pages' rooms and lines, worked out here; None when the document's were kept already.
    free_space: FreeSpace | None = field(repr=False)
    signed: bool  # whether the document holds a signature: every share says it alike
    attached: str  # the set of the user's copies it was judged with (`AttachedFiles.key`)


class FitRules(TypedDict):
    tolerance_pt: float
    room_slack_pt: float
    condense_limit: float
    shrink_floor: float


class CountedSentence(TypedDict):
    one: str
    other: str


class Sentences(TypedDict):
    missing: str
    not_in_font: str
    will_leave_out: str
    too_long: str
    substitute: str
    substitute_same_widths: str
    drawn_in_your_copy: str
    drawn_in_own_copy: str
    undo_redaction: str
    undo_redacted_words: str  # as undo_redaction, of a span with some words redacted
    split_made: str  # heard as a line is split at the caret
    split_joined: str  # heard as it's joined there again
    split_changed: str  # why the caret, among changed words, is no place to split
    split_cant_here: str  # why there's nothing to split where the caret is
    split_redacted: str  # why a line with redacted words isn't split
    reopened: str
    export_left_out: str
    page_left_out: str
    page_put_back: str
    how_to_fix: str
    how_to_fix_touch: str
    how_to_add: str
    how_to_add_touch: str
    new_text: str
    new_text_moves: str
    new_text_moves_touch: str
    form_field_not_edited: str
    empty: str
    edit_breaks_signature: str
    email_broken: str
    back_to: str
    changed_to: str
    put_back: str
    redacted: str
    already_redacted: str
    new_text_added: str
    new_text_moved: str
    new_text_taken_out: str
    new_text_set_in: str
    your_copy_in_use: str
    your_copy_removed: str
    warning_muted: str
    warning_unmuted: str
    downloaded: str
    find_nothing: str
    find_identical: CountedSentence
    find_different: CountedSentence
    find_places: CountedSentence
    find_some_different: CountedSentence
    find_empty: CountedSentence
    find_some_empty: CountedSentence
    changed_places: CountedSentence
    changes: CountedSentence
    changes_different: CountedSentence
    changes_similar: CountedSentence
    changes_some: CountedSentence
    changes_some_different: CountedSentence
    changes_some_similar: CountedSentence
    at_rest_different: CountedSentence
    at_rest_similar: CountedSentence
    changes_too_long: CountedSentence
    typed_identical: str
    typed_different: str
    typed_similar: str
    join_parts: str
    join_sentences: str
    options: dict[str, dict[str, str]]
    approximate: dict[ApproximateReason, str]
    characters: dict[str, str]


class DocumentNoticeInfo(MessageInfo):
    type: str
    detail: str


class Document(TypedDict):
    build: str
    pages: list[PageInfo]
    spans: list[SpanInfo]
    fonts: list[FontInfo]
    lines: list[LineInfo]
    signed: bool

    id: str
    expires_at: str
    fit: FitRules
    sentences: Sentences
    notices: list[DocumentNoticeInfo]


@dataclass(frozen=True, slots=True)
class Loaded:
    id: str
    folder: Path
    expires_at: float
