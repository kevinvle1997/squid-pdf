"""Turn an edit log into engine calls, in named steps.

`core` knows no Replace, so it imports no feature. The steps, in order: `resolve`,
`log_fits`, `plan`, then `run`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import assert_never

from squidpdf.core import (
    Engine,
    InvalidRequest,
    LineToDraw,
    Message,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    turn_added,
)
from squidpdf.editing.constants import REDRAW_REACH_EM
from squidpdf.editing.edits import (
    DeletePage,
    Edit,
    Insert,
    MovePage,
    PageOp,
    Redact,
    Replace,
    RotatePage,
    SpanEdit,
)
from squidpdf.editing.errors import BadReference, RedactionConflict
from squidpdf.editing.fit import Fitted, LogFits, insert_fit, insert_span, replace_fit
from squidpdf.editing.types import Applied, InsertNotice, Notice, Skipped, SpanNotice


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
class Resolved:
    """An edit list checked against the document: what each edit points at."""

    # The last edit to each span, in the order spans were first edited.
    span_edits: list[_EditedSpan]
    inserts: list[_ListedInsert]
    # Edits that point at nothing, left out and said why.
    skipped: list[Skipped]
    # Each edited span's room, read from the original page, by id.
    rooms: dict[str, float]
    # How many pages the original has, numbered from 0.
    page_count: int
    # The pages the page ops leave, in the order they leave them, by original number.
    order: list[int]
    # How far each page the ops turn is turned on top of its own turn, by original number.
    turns: dict[int, QuarterTurn]


@dataclass(frozen=True, slots=True)
class Erase:
    """A redaction: the span's text and its links go, and nothing is drawn in their place."""

    span: Span


@dataclass(frozen=True, slots=True, eq=False)
class _Redraw:
    """A replace: the span's text goes, and `text` is drawn in its place, as its fit says."""

    span: Span
    text: str
    fitted: Fitted


@dataclass(frozen=True, slots=True, eq=False)
class _Place:
    """An insert: new text drawn where there was none, as its fit says, turned with its page."""

    position: int
    span: Span
    fitted: Fitted
    turn_ccw: QuarterTurn


# What happens to the page for one edit.
type Step = Erase | _Redraw | _Place


def is_page(page: int, page_count: int) -> bool:
    """Whether the document has a page numbered `page`, counting from 0.

    Every edit, region and page list that names a page asks this. Where the
    answer is no, a bad edit is skipped and a request's own pages are refused.
    """
    return 0 <= page < page_count


def resolve(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> Resolved:
    """The list checked against the document: each edit with what it points at.

    A redaction that points at nothing raises BadReference instead of being
    skipped, and a replace after a redaction of the same text raises
    RedactionConflict: redaction wins, so the list's order can't undo one.

    A span edited twice is drawn once, in its final state: otherwise a second
    correction draws over the first, and a Redact after a Replace would leave
    the replacement showing while it's reported gone.
    """
    page_count = engine.page_count()
    spans = _SpanEdits(latest={})
    inserts: list[_ListedInsert] = []
    skipped: list[Skipped] = []
    pages = _PageOps(order=list(range(page_count)), turns={})
    for position, edit in enumerate(edits):
        # A page op naming pages the document has: folded into the pages kept, in list order.
        if isinstance(edit, RotatePage | DeletePage | MovePage) and _names_pages(
            edit, page_count
        ):
            skipped += [Skipped(position, "bad_reference", why) for why in pages.fold(edit)]
            continue
        # A page op naming a page it doesn't have.
        if isinstance(edit, RotatePage | DeletePage | MovePage):
            skipped.append(Skipped(position, "bad_reference", Message("no_page")))
            continue
        # An insert on a page the document has.
        if isinstance(edit, Insert) and is_page(edit.page, page_count):
            inserts.append(_ListedInsert(position, edit))
            continue
        # An insert on a page it doesn't have.
        if isinstance(edit, Insert):
            skipped.append(Skipped(position, "bad_reference", Message("no_page")))
            continue
        # An edit of a span: its last so far, if the document has the span.
        if isinstance(edit, SpanEdit):
            # .get: the browser can send a span id this document doesn't have.
            span = index.get(edit.span_id)
            said = spans.add(edit, span, position=position)
            skipped += [Skipped(position, "bad_reference", why) for why in said]
            continue
        assert_never(edit)
    span_edits = list(spans.latest.values())
    rooms = engine.rooms(index, [edited_span.span for edited_span in span_edits])
    return Resolved(
        span_edits,
        inserts=inserts,
        skipped=skipped,
        rooms=rooms,
        page_count=page_count,
        order=pages.order,
        turns=pages.turns,
    )


@dataclass(slots=True)
class _SpanEdits:
    """The span edits read so far: each span's last, by id, in the order first edited."""

    latest: dict[str, _EditedSpan]

    def add(self, edit: SpanEdit, span: Span | None, *, position: int) -> list[Message]:
        """Read one more edit of a span; returns why it's left out, if it is."""
        # A redaction of text it doesn't have: skipping it would be a leak.
        if isinstance(edit, Redact) and span is None:
            raise BadReference(edit.span_id)
        # A replace of text it doesn't have.
        if span is None:
            return [Message("no_span")]
        # A redaction of text the document has: later edits can't bring it back.
        if isinstance(edit, Redact):
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        # A replace of redacted text: the browser undoes a redaction by leaving it out.
        if self._redacted(edit.span_id):
            raise RedactionConflict(
                debug=f"edit {position} replaces span {edit.span_id}, redacted before"
            )
        # A replace of text the document has. Overwriting a key keeps its first place.
        if isinstance(edit, Replace):
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        assert_never(edit)

    def _redacted(self, span_id: str) -> bool:
        """Whether the span's last edit so far is a redaction: none after it can replace it."""
        # .get: a span not edited yet.
        last = self.latest.get(span_id)
        return last is not None and isinstance(last.edit, Redact)


def _names_pages(op: PageOp, page_count: int) -> bool:
    """Whether every page a page op names is one the document has."""
    before = op.before if isinstance(op, MovePage) else None
    return is_page(op.page, page_count) and (before is None or is_page(before, page_count))


@dataclass(slots=True)
class _PageOps:
    """The page ops folded so far: the pages kept, in order, and how far each is turned."""

    order: list[int]
    turns: dict[int, QuarterTurn]

    def fold(self, op: PageOp) -> list[Message]:
        """Fold one more op in; returns why it was left out, if it was.

        An op on a page deleted before it is moot, and ignored, as an edit on it is.
        """
        # A page deleted earlier in the list: there's nothing left to turn or move.
        if op.page not in self.order:
            return []
        # A turn: on top of any earlier ones, a whole turn coming back to none.
        if isinstance(op, RotatePage):
            self.turns[op.page] = turn_added(self.turns.get(op.page, 0), op.degrees)
            return []
        # The last page left: a file can't have none.
        if isinstance(op, DeletePage) and len(self.order) == 1:
            return [Message("last_page")]
        # A page left out.
        if isinstance(op, DeletePage):
            self.order.remove(op.page)
            return []
        # A move: before another page, or to the end.
        if isinstance(op, MovePage):
            self._move(op.page, before=op.before)
            return []
        assert_never(op)

    def _move(self, page: int, *, before: int | None) -> None:
        """Put `page` just before `before`, or last when it's None.

        Before itself, or before a page deleted earlier, it stays where it is: moot.
        """
        if before == page or (before is not None and before not in self.order):
            return
        self.order.remove(page)
        self.order.insert(len(self.order) if before is None else self.order.index(before), page)


def page_order(resolved: Resolved, pages: Sequence[int] | None) -> list[int]:
    """The pages the saved file has, in order, by their numbers in the original.

    Worked out once, so the pages kept and the redaction check read the same
    order: `pages` when the request names them, else the order the page ops leave.
    `pages` is the final order, so it may not name a page an edit deleted.
    """
    if pages is None:
        return list(resolved.order)
    deleted = sorted(set(pages) - set(resolved.order))
    if deleted:
        raise InvalidRequest(debug=f"pages: {deleted} deleted by an edit")
    return list(pages)


def redacted_in(resolved: Resolved) -> tuple[Span, ...]:
    """Every span whose last edit is a redaction, numbered as in the original."""
    return tuple(
        edited_span.span
        for edited_span in resolved.span_edits
        if isinstance(edited_span.edit, Redact)
    )


def log_fits(engine: Engine, resolved: Resolved) -> LogFits:
    """A fit, with its plan, for each replace and insert the log leaves, drawn or not.

    Measurement only. Call it before `run`: erasing can drop the fonts it measures with.
    """
    fits = {
        edited_span.span.id: _fit_of(
            engine, edited_span, room_pt=resolved.rooms[edited_span.span.id]
        )
        for edited_span in resolved.span_edits
    }
    return LogFits(
        replaces={span_id: fit for span_id, fit in fits.items() if fit is not None},
        inserts={
            listed.position: insert_fit(engine, listed.insert) for listed in resolved.inserts
        },
    )


def _fit_of(engine: Engine, edited_span: _EditedSpan, *, room_pt: float) -> Fitted | None:
    """What a span edit will look like, in its room; None for one that draws nothing to fit."""
    edit = edited_span.edit
    # A replace: measured as it will be drawn.
    if isinstance(edit, Replace):
        span = edited_span.span
        return replace_fit(engine, span, edit.text, room_pt=room_pt, strategy=edit.strategy)
    # A redaction: nothing is drawn.
    if isinstance(edit, Redact):
        return None
    assert_never(edit)


def plan(
    engine: Engine,
    resolved: Resolved,
    fits: LogFits,
    *,
    strips: Mapping[int, list[Rect]] | None = None,
) -> list[Step]:
    """The steps the edits shown take, each drawn with its fit's plan.

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
    # New text reads upright as the page is shown: turned back as far as its page turns,
    # an edit's turn included.
    turns_cw = (
        [
            turn_added(page.turn_cw, resolved.turns.get(number, 0))
            for number, page in enumerate(engine.pages())
        ]
        if placed
        else []
    )
    return [
        *(_step_for(edited_span, fits) for edited_span in shown),
        *(
            _Place(
                listed.position,
                insert_span(listed.insert),
                fits.inserts[listed.position],
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


def _step_for(edited_span: _EditedSpan, fits: LogFits) -> Step:
    """What one span edit does to the page."""
    edit = edited_span.edit
    span = edited_span.span
    # A redaction: its text goes, and nothing is drawn.
    if isinstance(edit, Redact):
        return Erase(span)
    # A replace: its text goes, and the new text is drawn as its fit says.
    if isinstance(edit, Replace):
        return _Redraw(span, edit.text, fits.replaces[span.id])
    assert_never(edit)


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
    redacted = [step.span for step in steps if isinstance(step, Erase)]
    # A redaction's links (a mailto: can carry its text) and hidden copies go too.
    engine.unlink(redacted)
    engine.drop_hidden_copies(redacted)
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
        return LineToDraw(step.span, step.text, step.fitted.plan)
    # An insert: its text, where there was none.
    if isinstance(step, _Place):
        return LineToDraw(step.span, step.span.text, step.fitted.plan)
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
        fitted = step.fitted
        drawn = engine.draw(
            step.span,
            step.text,
            plan=fitted.plan,
            size=fitted.size,
            scale_x=fitted.scale_x,
        )
        return [SpanNotice(step.span.id, said) for said in drawn]
    # An insert: new text drawn where there was none.
    if isinstance(step, _Place):
        drawn = engine.draw(
            step.span, step.span.text, plan=step.fitted.plan, turn_ccw=step.turn_ccw
        )
        return [InsertNotice(step.position, said) for said in drawn]
    assert_never(step)


def apply_edits(engine: Engine, resolved: Resolved) -> Applied:
    """Apply every edit on every page, in memory. Nothing is written."""
    fits = log_fits(engine, resolved)  # before run: erasing can drop the fonts it measures
    return Applied(resolved.skipped, run(engine, plan(engine, resolved, fits)))
