"""Turn an edit log into engine calls, in named steps.

`core` knows no Replace, so it imports no feature. The steps, in order: `resolve`,
`log_fits`, `plan`, then `run`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import assert_never

from squidpdf.core import (
    Engine,
    Face,
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
from squidpdf.editing.fit import (
    Fitted,
    LogFits,
    Verdict,
    insert_fit,
    insert_span,
    replace_fit,
)
from squidpdf.editing.types import Applied, InsertNotice, Notice, Replaced, Skipped, SpanNotice


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
class _Erase:
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
type Step = _Erase | _Redraw | _Place


def is_page(page: int, page_count: int) -> bool:
    """Whether the document has a page numbered `page`, counting from 0.

    Every edit, region and page list that names a page asks this. Where the
    answer is no, a bad edit is skipped and a request's own pages are refused.
    """
    return 0 <= page < page_count


def resolve(
    engine: Engine, index: SpanIndex, *, rooms: Mapping[str, float], edits: Sequence[Edit]
) -> Resolved:
    """The list checked against the document: each edit with what it points at.

    A redaction that points at nothing raises BadReference, and a replace after a redaction of
    the same text raises RedactionConflict: the order can't undo one. A span edited twice is
    drawn once, as its last edit leaves it, or a Redact after a Replace would show text gone.
    """
    page_count = engine.page_count()
    listed = _EditList(
        page_count,
        spans=_SpanEdits(latest={}),
        pages=_PageOps(order=list(range(page_count)), turns={}),
        inserts=[],
    )
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        left_out = listed.read(edit, index=index, position=position)
        skipped += [Skipped(position, "bad_reference", why) for why in left_out]
    span_edits = list(listed.spans.latest.values())
    return Resolved(
        span_edits,
        inserts=listed.inserts,
        skipped=skipped,
        # Of the rooms the caller read from the original, only the edited spans'.
        rooms={edited_span.span.id: rooms[edited_span.span.id] for edited_span in span_edits},
        page_count=page_count,
        order=listed.pages.order,
        turns=listed.pages.turns,
    )


@dataclass(slots=True)
class _EditList:
    """The edit list read so far, by kind: span edits, inserts and page ops."""

    page_count: int
    spans: _SpanEdits
    pages: _PageOps
    inserts: list[_ListedInsert]

    def read(self, edit: Edit, *, index: SpanIndex, position: int) -> list[Message]:
        """Read one more edit; returns why it's left out, if it is."""
        # Folded into the pages kept, in list order.
        if isinstance(edit, RotatePage | DeletePage | MovePage) and _names_pages(
            edit, self.page_count
        ):
            return self.pages.fold(edit)
        # A page op naming a page it doesn't have.
        if isinstance(edit, RotatePage | DeletePage | MovePage):
            return [Message("no_page")]
        if isinstance(edit, Insert) and is_page(edit.page, self.page_count):
            self.inserts.append(_ListedInsert(position, edit))
            return []
        # An insert on a page it doesn't have.
        if isinstance(edit, Insert):
            return [Message("no_page")]
        # An edit of a span: its last so far, if the document has the span.
        if isinstance(edit, SpanEdit):
            # .get: the browser can send a span id this document doesn't have.
            return self.spans.add(edit, index.get(edit.span_id), position=position)
        assert_never(edit)


@dataclass(slots=True)
class _SpanEdits:
    """The span edits read so far: each span's last, by id, in the order first edited."""

    latest: dict[str, _EditedSpan]

    def add(self, edit: SpanEdit, span: Span | None, *, position: int) -> list[Message]:
        """Read one more edit of a span; returns why it's left out, if it is."""
        # Skipping it would be a leak.
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
        # On top of any earlier ones, a whole turn coming back to none.
        if isinstance(op, RotatePage):
            self.turns[op.page] = turn_added(self.turns.get(op.page, 0), op.degrees)
            return []
        # The last page left: a file can't have none.
        if isinstance(op, DeletePage) and len(self.order) == 1:
            return [Message("last_page")]
        if isinstance(op, DeletePage):
            self.order.remove(op.page)
            return []
        # Before another page, or to the end.
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
    if isinstance(edit, Replace):
        span = edited_span.span
        return replace_fit(engine, span, edit.text, room_pt=room_pt, strategy=edit.strategy)
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
                insert_span(engine, listed.insert),
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
    if isinstance(edit, Redact):
        return _Erase(span)
    if isinstance(edit, Replace):
        return _Redraw(span, edit.text, fits.replaces[span.id])
    assert_never(edit)


@dataclass(frozen=True, slots=True)
class Ran:
    """What doing the steps came out with: what came out other than asked, and each draw.

    `redrawn` is each replace drawn, by span id, and `placed` each insert, by its place:
    the face we ship that drew it, None for its own font (new text's face, where it draws).
    A replace the erase couldn't clear isn't drawn, so isn't there.
    """

    notices: list[Notice]
    redrawn: dict[str, Face | None]
    placed: dict[int, Face | None]


def run(engine: Engine, steps: Sequence[Step]) -> Ran:
    """Do the steps; returns what came out other than asked, and what drew each edit.

    Every erase happens in one pass before any draw: PyMuPDF erases a page at a
    time, and an erase after a redraw would take the new text too.
    """
    erased = [span for span in map(_erased_by, steps) if span is not None]
    drawn = [line for line in map(_drawn_by, steps) if line is not None]
    # Old text the erase couldn't clear, as a form field's: the field draws it, not the page.
    # Only a redraw reads it: a redaction's is checked where it's said, by RedactionController.
    stuck = {span.id for span in engine.remove(erased, then_drawn=drawn)}
    redacted = redacted_by(steps)
    # A redaction's links (a mailto: can carry its text) and hidden copies go too.
    engine.unlink(redacted)
    engine.drop_hidden_copies(redacted)
    finished = [_finish_step(engine, step, stuck=stuck) for step in steps]
    return Ran(
        [notice for ran in finished for notice in ran.notices],
        redrawn={span_id: face for ran in finished for span_id, face in ran.redrawn.items()},
        placed={position: face for ran in finished for position, face in ran.placed.items()},
    )


def redacted_by(steps: Sequence[Step]) -> list[Span]:
    """The spans the steps redact, in order."""
    return [step.span for step in steps if isinstance(step, _Erase)]


def _erased_by(step: Step) -> Span | None:
    """The span whose text a step erases first; None for one that only draws."""
    if isinstance(step, _Erase | _Redraw):
        return step.span
    # An insert: there was no text there.
    if isinstance(step, _Place):
        return None
    assert_never(step)


def _drawn_by(step: Step) -> LineToDraw | None:
    """The line a step draws once the erasing is done; None for one that only erases."""
    if isinstance(step, _Erase):
        return None
    if isinstance(step, _Redraw):
        return LineToDraw(step.span, step.text, step.fitted.plan)
    if isinstance(step, _Place):
        return LineToDraw(step.span, step.span.text, step.fitted.plan)
    assert_never(step)


def _finish_step(engine: Engine, step: Step, *, stuck: set[str]) -> Ran:
    """Do what a step does once the erasing is done; returns what came out, as `run` does.

    `stuck` are the spans whose text the erase couldn't clear.
    """
    if isinstance(step, _Erase):
        return Ran([], redrawn={}, placed={})
    # A replace of text the erase couldn't clear: drawn, it would sit on the old text.
    if isinstance(step, _Redraw) and step.span.id in stuck:
        stuck_notice = SpanNotice(step.span.id, Message("form_field_not_edited"))
        return Ran([stuck_notice], redrawn={}, placed={})
    if isinstance(step, _Redraw):
        fitted = step.fitted
        drawn = engine.draw(
            step.span,
            step.text,
            plan=fitted.plan,
            size=fitted.size,
            scale_x=fitted.scale_x,
        )
        notices: list[Notice] = [SpanNotice(step.span.id, said) for said in drawn.notices]
        return Ran(notices, redrawn={step.span.id: drawn.face}, placed={})
    if isinstance(step, _Place):
        drawn = engine.draw(
            step.span, step.span.text, plan=step.fitted.plan, turn_ccw=step.turn_ccw
        )
        # New text in a face we ship is in its own font where that face draws it.
        asked = drawn.face is not None and drawn.face.name == step.span.font
        face = None if asked else drawn.face
        insert_notices: list[Notice] = [
            InsertNotice(step.position, said) for said in drawn.notices
        ]
        return Ran(insert_notices, redrawn={}, placed={step.position: face})
    assert_never(step)


def apply_edits(engine: Engine, resolved: Resolved) -> Applied:
    """Apply every edit on every page, in memory. Nothing is written."""
    fits = log_fits(engine, resolved)  # before run: erasing can drop the fonts it measures
    rewritten = _rewritten(resolved)
    # Before run too, for the same reason.
    shown_names = {span.id: engine.shown_name(span) for span in rewritten}
    ran = run(engine, plan(engine, resolved, fits))
    verdicts = verdicts_of(fits, ran)
    replaced = [
        Replaced(verdicts.replaces[span.id], shown_names[span.id])
        for span in rewritten
        if span.id in ran.redrawn
    ]
    return Applied(resolved.skipped, ran.notices, replaced)


def _rewritten(resolved: Resolved) -> list[Span]:
    """Each span whose last edit replaces its words with others.

    A replace that types a span's own words back draws it as it was: no edit to count.
    """
    return [
        edited_span.span
        for edited_span in resolved.span_edits
        if isinstance(edited_span.edit, Replace)
        and edited_span.edit.text != edited_span.span.text
    ]


@dataclass(frozen=True, slots=True)
class Verdicts:
    """How each edit came out: a replace by its span's id, an insert by its place in the log."""

    replaces: dict[str, Verdict]
    inserts: dict[int, Verdict]


def verdicts_of(fits: LogFits, ran: Ran) -> Verdicts:
    """How each edit came out: as drawn where `run` drew it, else as planned.

    The one way render and export judge an edit, so the note and the export's log agree.
    """
    # .get: an edit on a page this run didn't draw has only its plan to go by.
    return Verdicts(
        replaces={
            span_id: _as_drawn(fitted.verdict, ran.redrawn.get(span_id))
            for span_id, fitted in fits.replaces.items()
        },
        inserts={
            position: _as_drawn(fitted.verdict, ran.placed.get(position))
            for position, fitted in fits.inserts.items()
        },
    )


def _as_drawn(planned: Verdict, face: Face | None) -> Verdict:
    """An edit as it was drawn: a face that drew it, the page having refused the font, too."""
    return planned if face is None else replace(planned, fidelity="substitute", why=None)
