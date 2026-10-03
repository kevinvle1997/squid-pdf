"""Turn an edit log into engine calls, in named steps.

The engine knows `remove` and `draw`. It does not know what a Replace is, which
is what keeps `core` free of any feature import. Translating one into the other
is this module's whole job:

    resolved = resolve(engine, edits, index)  # once: what each edit points at
    fits = log_fits(engine, resolved)         # what each will look like
    steps = plan(engine, resolved)            # Erase, Redraw or Place, per edit shown
    notices = run(engine, steps)              # every erase, then every draw
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import assert_never

from squidpdf.core import (
    FACES,
    Engine,
    LineToDraw,
    Message,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    index_of,
    new_text,
)
from squidpdf.editing.constants import REDRAW_REACH_EM
from squidpdf.editing.edits import Edit, Insert, Redact, Replace, SpanEdit
from squidpdf.editing.errors import BadReference, RedactionConflict
from squidpdf.editing.fit import FitReport, LogFits, Option, options_for
from squidpdf.editing.types import Applied, InsertNotice, Notice, Skipped, SpanNotice, Strategy


@dataclass(frozen=True, slots=True)
class _EditedSpan:
    """A span's last edit in the list, with the span it points at."""

    edit: SpanEdit
    span: Span


@dataclass(frozen=True, slots=True)
class _ListedInsert:
    """An insert on a page the document has, with its place in the list the browser sent."""

    position: int
    insert: Insert


@dataclass(frozen=True, slots=True)
class _Resolved:
    """An edit list checked against the document: what each edit points at."""

    # The last edit to each span, in the order spans were first edited.
    span_edits: list[_EditedSpan]
    inserts: list[_ListedInsert]
    # Edits that point at nothing, left out and said why.
    skipped: list[Skipped]
    # How many pages the original has, numbered from 0.
    page_count: int


@dataclass(frozen=True, slots=True)
class Erase:
    """A redaction: the span's text and its links go, and nothing is drawn in their place."""

    span: Span


@dataclass(frozen=True, slots=True)
class _Redraw:
    """A replace: the span's text goes, and `text` is drawn in its place.

    A None `size` keeps the span's own; `scale_x` narrows the line from its start.
    """

    span: Span
    text: str
    size: float | None
    scale_x: float


@dataclass(frozen=True, slots=True)
class _Place:
    """An insert: new text drawn where there was none, turned as its page is turned."""

    position: int
    span: Span
    turn_ccw: QuarterTurn


# What happens to the page for one edit.
type Step = Erase | _Redraw | _Place


def is_page(page: int, page_count: int) -> bool:
    """Whether the document has a page numbered `page`, counting from 0.

    Every edit, region and page list that names a page asks this. Where the
    answer is no, a bad edit is skipped and a request's own pages are refused.
    """
    return 0 <= page < page_count


def resolve(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> _Resolved:
    """The list checked against the document: each edit with what it points at.

    A redaction that points at nothing raises BadReference instead of being
    skipped, and a replace after a redaction of the same text raises
    RedactionConflict: redaction wins, so the list's order can't undo one.

    A span edited twice is drawn once, in its final state: otherwise a second
    correction draws over the first, and a Redact after a Replace would leave
    the replacement showing while it's reported gone.
    """
    page_count = engine.page_count()
    # Overwriting a key keeps its place, so spans stay in the order first edited.
    latest: dict[str, _EditedSpan] = {}
    redacted: set[str] = set()
    inserts: list[_ListedInsert] = []
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        # An insert on a page the document has.
        if isinstance(edit, Insert) and is_page(edit.page, page_count):
            inserts.append(_ListedInsert(position, edit))
            continue
        # An insert on a page it doesn't have.
        if isinstance(edit, Insert):
            skipped.append(Skipped(position, "bad_reference", Message("no_page")))
            continue
        # .get: the browser can send a span id this document doesn't have.
        span = index.get(edit.span_id)
        # A redaction of text it doesn't have: skipping it would be a leak.
        if isinstance(edit, Redact) and span is None:
            raise BadReference(edit.span_id)
        # A replace of text it doesn't have.
        if span is None:
            skipped.append(Skipped(position, "bad_reference", Message("no_span")))
            continue
        # A redaction of text the document has: later edits can't bring it back.
        if isinstance(edit, Redact):
            redacted.add(edit.span_id)
            latest[edit.span_id] = _EditedSpan(edit, span)
            continue
        # A replace of redacted text: the browser undoes a redaction by leaving it out.
        if edit.span_id in redacted:
            raise RedactionConflict(
                debug=f"edit {position} replaces span {edit.span_id}, redacted before"
            )
        # A replace of text the document has.
        if isinstance(edit, Replace):
            latest[edit.span_id] = _EditedSpan(edit, span)
            continue
        assert_never(edit)
    return _Resolved(list(latest.values()), inserts, skipped, page_count)


def page_order(resolved: _Resolved, pages: Sequence[int] | None) -> list[int]:
    """The pages the saved file has, in order, by their numbers in the original.

    Worked out once, so the pages kept and the redaction check read the same
    order: `pages` when the request names them, else every page where it was.
    """
    return list(range(resolved.page_count)) if pages is None else list(pages)


def redacted_in(resolved: _Resolved) -> tuple[Span, ...]:
    """Every span whose last edit is a redaction, numbered as in the original."""
    return tuple(
        edited_span.span
        for edited_span in resolved.span_edits
        if isinstance(edited_span.edit, Redact)
    )


def log_fits(engine: Engine, resolved: _Resolved) -> LogFits:
    """A fit for each span the log leaves replaced and for each insert, drawn or not.

    Measurement only. Call it before `run`: erasing can drop the fonts it measures with.
    """
    fits = {
        edited_span.span.id: _fit_of(engine, edited_span) for edited_span in resolved.span_edits
    }
    return LogFits(
        replaces={span_id: fit for span_id, fit in fits.items() if fit is not None},
        inserts={
            listed.position: insert_fit(engine, listed.insert) for listed in resolved.inserts
        },
    )


def _fit_of(engine: Engine, edited_span: _EditedSpan) -> FitReport | None:
    """What a span edit will look like; None for one that draws nothing to fit."""
    edit = edited_span.edit
    # A replace: measured as it will be drawn.
    if isinstance(edit, Replace):
        return replace_fit(engine, edited_span.span, edit.text, strategy=edit.strategy)
    # A redaction: nothing is drawn.
    if isinstance(edit, Redact):
        return None
    assert_never(edit)


def plan(
    engine: Engine, resolved: _Resolved, *, strips: Mapping[int, list[Rect]] | None = None
) -> list[Step]:
    """The steps the edits shown take, worked out before anything is erased.

    `strips` are the rows drawn, by page, or None to draw every edit. A span
    edit shows where its rows meet a strip; turned text, whose redraw is level,
    and inserts show anywhere on a drawn page.
    """
    shown = [
        edited_span
        for edited_span in resolved.span_edits
        if strips is None or _shows(strips, edited_span.span)
    ]
    placed = [
        listed for listed in resolved.inserts if strips is None or listed.insert.page in strips
    ]
    # New text reads upright as the page is shown: turned back as far as its page turns.
    turns_cw = [page.turn_cw for page in engine.pages()] if placed else []
    return [
        *(_step_for(engine, edited_span) for edited_span in shown),
        *(
            _Place(
                listed.position,
                _insert_span(listed.insert),
                turn_ccw=turns_cw[listed.insert.page],
            )
            for listed in placed
        ),
    ]


def _shows(strips: Mapping[int, list[Rect]], span: Span) -> bool:
    """Whether an edit to `span` shows in the strips drawn on its page."""
    # .get: a page with no strip drawn on it shows no edit.
    on_page = strips.get(span.page, [])
    # Turned text is redrawn level, so its box says little of where: anywhere on a drawn page.
    if on_page and span.turned:
        return True
    # Letters drawn at the span's size can reach a little past its box: an accent, a tail.
    reach = span.size * REDRAW_REACH_EM
    top, bottom = span.bbox.y0 - reach, span.bbox.y1 + reach
    return any(strip.y0 < bottom and top < strip.y1 for strip in on_page)


def _step_for(engine: Engine, edited_span: _EditedSpan) -> Step:
    """What one span edit does to the page."""
    edit = edited_span.edit
    # A redaction: its text goes, and nothing is drawn.
    if isinstance(edit, Redact):
        return Erase(edited_span.span)
    # A replace: its text goes, and the new text is drawn.
    if isinstance(edit, Replace):
        return _redraw_of(engine, edited_span.span, edit)
    assert_never(edit)


def _redraw_of(engine: Engine, span: Span, replace: Replace) -> _Redraw:
    """A replacement at the size and width it's drawn: its own, or shrunk or condensed to fit.

    The edit's strategy counts only if it was offered, as its fit says.
    """
    original_width = engine.measure(span, span.text)
    typed_width = engine.measure(span, replace.text)
    delta_pt = typed_width - original_width
    strategy = _strategy_drawn(replace.strategy, options_for(delta_pt, original_width))
    # Drawn as typed: the span's size, no stretch.
    if strategy == "as-is":
        return _Redraw(span, replace.text, size=None, scale_x=1.0)
    # Smaller letters, same shape: width goes with size, so it ends where the original did.
    if strategy == "shrink":
        return _Redraw(
            span, replace.text, size=span.size * original_width / typed_width, scale_x=1.0
        )
    # The same size, letters squeezed narrower, to the same end.
    if strategy == "condense":
        return _Redraw(span, replace.text, size=None, scale_x=original_width / typed_width)
    assert_never(strategy)


def _strategy_drawn(asked: Strategy, options: list[Option]) -> Strategy:
    """The way out that's drawn: the one asked for if it was offered, else as-is."""
    offered = asked in {option.name for option in options}
    return asked if offered else "as-is"


def run(engine: Engine, steps: Sequence[Step]) -> list[Notice]:
    """Do the steps; returns what came out other than asked.

    Every erase happens in one pass before any draw: PyMuPDF erases a page at a
    time, and an erase after a redraw would take the new text too.
    """
    erased = [span for span in map(_erased_by, steps) if span is not None]
    drawn = [line for line in map(_drawn_by, steps) if line is not None]
    # Old text the erase couldn't clear, as a form field's: the field draws it, not the page.
    # Only a redraw reads it: a redaction's is checked where it's said, by RedactionController.
    stuck = {span.id for span in engine.remove(erased, then_drawn=drawn)}
    # A redaction's links go too, as one can carry the text it's on (a mailto:).
    engine.unlink([step.span for step in steps if isinstance(step, Erase)])
    return [notice for step in steps for notice in _finish_step(engine, step, stuck=stuck)]


def _erased_by(step: Step) -> Span | None:
    """The span whose text a step erases first; None for one that only draws."""
    # A redaction or a replace: the span's own text goes first.
    if isinstance(step, Erase | _Redraw):
        return step.span
    # An insert: there was no text there.
    if isinstance(step, _Place):
        return None
    assert_never(step)


def _drawn_by(step: Step) -> LineToDraw | None:
    """The line a step draws once the erasing is done; None for one that only erases."""
    # A redaction: nothing drawn.
    if isinstance(step, Erase):
        return None
    # A replace: its new text, where the old was.
    if isinstance(step, _Redraw):
        return LineToDraw(step.span, step.text)
    # An insert: its text, where there was none.
    if isinstance(step, _Place):
        return LineToDraw(step.span, step.span.text)
    assert_never(step)


def _finish_step(engine: Engine, step: Step, *, stuck: set[str]) -> list[Notice]:
    """Do what a step does once the erasing is done; returns what came out other than asked.

    `stuck` are the spans whose text the erase couldn't clear.
    """
    # A redaction: nothing to draw.
    if isinstance(step, Erase):
        return []
    # A replace of text the erase couldn't clear: drawn, it would sit on the old text.
    if isinstance(step, _Redraw) and step.span.id in stuck:
        return [SpanNotice(step.span.id, Message("form_field_not_edited"))]
    # A replace: the new text drawn where the old was.
    if isinstance(step, _Redraw):
        drawn = engine.draw(step.span, step.text, size=step.size, scale_x=step.scale_x)
        return [SpanNotice(step.span.id, said) for said in drawn]
    # An insert: new text drawn where there was none.
    if isinstance(step, _Place):
        drawn = engine.draw(step.span, step.span.text, turn_ccw=step.turn_ccw)
        return [InsertNotice(step.position, said) for said in drawn]
    assert_never(step)


def apply_edits(engine: Engine, resolved: _Resolved) -> Applied:
    """Apply every edit on every page, in memory. Nothing is written.

    And a redaction's words go from the hidden copies on its page. Only here,
    for a file that's saved: a render draws none of them, so removing them
    changes nothing it shows.
    """
    applied = Applied(resolved.skipped, run(engine, plan(engine, resolved)))
    engine.drop_hidden_copies(list(redacted_in(resolved)))
    return applied


def _insert_span(insert: Insert) -> Span:
    """An insert as a span, so it's judged, measured and drawn exactly like an edit."""
    return new_text(
        insert.page,
        origin=insert.origin,
        text=insert.text,
        size=insert.size,
        font=insert.font,
        color=insert.color,
    )


def insert_fit(engine: Engine, insert: Insert) -> FitReport:
    """What new text will really look like: in its chosen font, or what draws it instead.

    Nothing to fit against, so only the font and the letters are checked.
    """
    span = _insert_span(insert)
    [report] = engine.assess(index_of([span]))
    shipped = insert.font in FACES
    # Not a face we ship, and not a font of this page's we can use: it can't be used at all.
    # One that only lacks a letter can: the substitute draws that line, as for a replace.
    unusable = not shipped and not report.in_file
    typed_plan = engine.plan_for(span, insert.text)
    return FitReport(
        delta_pt=0.0,
        missing=[] if unusable else typed_plan.missing,
        left_out=typed_plan.left_out,
        substitute=engine.substitute(span, insert.text, plan=typed_plan),
        unavailable=insert.font if unusable else "",
    )


def replace_fit(
    engine: Engine, span: Span, text: str, *, strategy: Strategy = "as-is"
) -> FitReport:
    """What would happen if the user typed this, with the ways out if it will not fit.

    `strategy` is kept only if it's one of the ways out offered; otherwise as-is.
    """
    # One plan for what's typed, asked everything the fit says; the original's is its own.
    typed_plan = engine.plan_for(span, text)
    original_width = engine.measure(span, span.text)
    typed_width = engine.width_of(span, typed_plan)
    delta_pt = typed_width - original_width
    options = options_for(delta_pt, original_width)
    return FitReport(
        delta_pt=round(delta_pt, 2),
        missing=typed_plan.missing,
        options=options,
        strategy=_strategy_drawn(strategy, options),
        left_out=typed_plan.left_out,
        substitute=engine.substitute(span, text, plan=typed_plan),
        asked=strategy,
    )
