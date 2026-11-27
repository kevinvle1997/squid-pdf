"""`core` knows no Replace, so it imports no feature."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import assert_never

from squidpdf.core import (
    DrawnLine,
    Engine,
    Face,
    FreeSpace,
    InvalidRequest,
    Line,
    LineToDraw,
    Message,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    turn_added,
)
from squidpdf.documents.errors import is_page
from squidpdf.editing import constants
from squidpdf.editing.edits import (
    DeletePage,
    Edit,
    Insert,
    MovePage,
    PageOp,
    Redact,
    Replace,
    ReplaceRuns,
    RotatePage,
    SpanEdit,
)
from squidpdf.editing.errors import BadReference, RedactionConflict
from squidpdf.editing.fit import (
    EditFits,
    Fitted,
    LineFitted,
    RunFitted,
    Verdict,
    insert_fit,
    insert_span,
    line_fit,
    replace_fit,
)
from squidpdf.editing.redacted_words import merged_ranges, range_of, redacted_of, runs_of
from squidpdf.editing.types import (
    Applied,
    EditedPage,
    InsertNotice,
    Notice,
    Redacted,
    Replaced,
    Skipped,
    SpanNotice,
)


@dataclass(frozen=True, slots=True)
class _EditedSpan:
    edit: SpanEdit
    span: Span


@dataclass(frozen=True, slots=True, eq=False)
class _EditedLine:
    edit: ReplaceRuns
    spans: tuple[Span, ...]
    room_pt: float


@dataclass(frozen=True, slots=True)
class _ListedInsert:
    position: int
    insert: Insert


@dataclass(frozen=True, slots=True)
class Resolved:
    span_edits: list[_EditedSpan]
    line_edits: list[_EditedLine]
    inserts: list[_ListedInsert]
    skipped: list[Skipped]
    rooms: dict[str, float]
    page_count: int
    order: list[int]
    turns: dict[int, QuarterTurn]
    redacted: dict[str, Redacted]


@dataclass(frozen=True, slots=True)
class _Erase:
    redacted: Redacted


@dataclass(frozen=True, slots=True, eq=False)
class _Redraw:
    span: Span
    text: str
    fitted: Fitted


@dataclass(frozen=True, slots=True, eq=False)
class _RedrawRuns:
    fitted: LineFitted


@dataclass(frozen=True, slots=True, eq=False)
class _Place:
    position: int
    span: Span
    fitted: Fitted
    turn_ccw: QuarterTurn


type Step = _Erase | _Redraw | _RedrawRuns | _Place


def resolve(
    engine: Engine, index: SpanIndex, *, free_space: FreeSpace, edits: Sequence[Edit]
) -> Resolved:
    """A replace after a redaction of its text raises RedactionConflict: order can't undo it."""
    page_count = engine.page_count()
    listed = _EditList(
        page_count,
        spans=_SpanEdits(latest={}, redacted_ranges={}),
        pages=_PageOps(order=list(range(page_count)), turns={}),
        inserts=[],
        lines={line.span_ids: line for line in free_space.lines},
    )
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        left_out = listed.read(edit, index=index, position=position)
        skipped += [Skipped(position, "bad_reference", why) for why in left_out]
    latest = listed.spans.latest.values()
    span_edits = [edited for edited in latest if isinstance(edited, _EditedSpan)]
    line_edits = list(
        dict.fromkeys(edited for edited in latest if isinstance(edited, _EditedLine))
    )
    rooms = free_space.rooms
    return Resolved(
        span_edits,
        line_edits=line_edits,
        inserts=listed.inserts,
        skipped=skipped,
        rooms={edited_span.span.id: rooms[edited_span.span.id] for edited_span in span_edits},
        page_count=page_count,
        order=listed.pages.order,
        turns=listed.pages.turns,
        redacted={
            span_id: redacted_of(engine, index.by_id[span_id], ranges)
            for span_id, ranges in listed.spans.redacted_ranges.items()
        },
    )


@dataclass(slots=True)
class _EditList:
    page_count: int
    spans: _SpanEdits
    pages: _PageOps
    inserts: list[_ListedInsert]
    lines: dict[tuple[str, ...], Line]

    def read(self, edit: Edit, *, index: SpanIndex, position: int) -> list[Message]:
        if isinstance(edit, PageOp) and _names_pages(edit, self.page_count):
            return self.pages.fold(edit)
        if isinstance(edit, PageOp):
            return [Message("no_page")]
        if isinstance(edit, Insert) and is_page(edit.page, self.page_count):
            self.inserts.append(_ListedInsert(position, edit))
            return []
        if isinstance(edit, Insert):
            return [Message("no_page")]
        if isinstance(edit, SpanEdit):
            return self.spans.add(edit, index.by_id.get(edit.span_id), position=position)
        if isinstance(edit, ReplaceRuns):
            line = self.lines.get(tuple(run.span_id for run in edit.runs))
            if line is None:
                return [Message("not_one_line")]
            spans = tuple(index.by_id[span_id] for span_id in line.span_ids)
            return self.spans.add_line(
                _EditedLine(edit, spans, line.room_pt), position=position
            )
        assert_never(edit)


@dataclass(slots=True)
class _SpanEdits:
    latest: dict[str, _EditedSpan | _EditedLine]
    redacted_ranges: dict[str, tuple[range, ...]]

    def add(self, edit: SpanEdit, span: Span | None, *, position: int) -> list[Message]:
        # Skipping it would be a leak.
        if isinstance(edit, Redact) and span is None:
            raise BadReference(edit.span_id)
        if span is None:
            return [Message("no_span")]
        if isinstance(edit, Redact):
            earlier = self.redacted_ranges.get(edit.span_id, ())
            ranges = merged_ranges([*earlier, range_of(edit, span)])
            self.redacted_ranges[edit.span_id] = ranges
            self._forget_line_of(edit.span_id)
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        # A replace of redacted text: the browser undoes a redaction by leaving it out.
        if self._redacted(edit.span_id):
            raise RedactionConflict(
                debug=f"edit {position} replaces span {edit.span_id}, redacted before"
            )
        if isinstance(edit, Replace):
            self._forget_line_of(edit.span_id)
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        assert_never(edit)

    def add_line(self, edited_line: _EditedLine, *, position: int) -> list[Message]:
        redacted = [span.id for span in edited_line.spans if self._redacted(span.id)]
        if redacted:
            raise RedactionConflict(
                debug=f"edit {position} replaces span {redacted[0]}, redacted before"
            )
        for span in edited_line.spans:
            self._forget_line_of(span.id)
            self.latest[span.id] = edited_line
        return []

    def _forget_line_of(self, span_id: str) -> None:
        """A line is drawn whole or not: a later edit to any of its spans takes it all."""
        last = self.latest.get(span_id)
        if not isinstance(last, _EditedLine):
            return
        for span in last.spans:
            del self.latest[span.id]

    def _redacted(self, span_id: str) -> bool:
        last = self.latest.get(span_id)
        return isinstance(last, _EditedSpan) and isinstance(last.edit, Redact)


def _names_pages(op: PageOp, page_count: int) -> bool:
    before = op.before if isinstance(op, MovePage) else None
    return is_page(op.page, page_count) and (before is None or is_page(before, page_count))


@dataclass(slots=True)
class _PageOps:
    order: list[int]
    turns: dict[int, QuarterTurn]

    def fold(self, op: PageOp) -> list[Message]:
        if op.page not in self.order:
            return []
        if isinstance(op, RotatePage):
            self.turns[op.page] = turn_added(self.turns.get(op.page, 0), op.degrees)
            return []
        # The last page left: a file can't have none.
        if isinstance(op, DeletePage) and len(self.order) == 1:
            return [Message("last_page")]
        if isinstance(op, DeletePage):
            self.order.remove(op.page)
            return []
        if isinstance(op, MovePage):
            self._move(op.page, before=op.before)
            return []
        assert_never(op)

    def _move(self, page: int, *, before: int | None) -> None:
        if before == page or (before is not None and before not in self.order):
            return
        self.order.remove(page)
        self.order.insert(len(self.order) if before is None else self.order.index(before), page)


def page_order(resolved: Resolved, pages: Sequence[int] | None) -> list[int]:
    """Worked out once, so the pages kept and the redaction check read the same order."""
    if pages is None:
        return list(resolved.order)
    deleted = sorted(set(pages) - set(resolved.order))
    if deleted:
        raise InvalidRequest(debug=f"pages: {deleted} deleted by an edit")
    return list(pages)


def redacted_in(resolved: Resolved) -> tuple[Redacted, ...]:
    return tuple(resolved.redacted.values())


def new_text_turns(engine: Engine, resolved: Resolved) -> list[QuarterTurn]:
    return [
        turn_added(page.turn_cw, resolved.turns.get(number, 0))
        for number, page in enumerate(engine.pages())
    ]


def fits_of(engine: Engine, resolved: Resolved) -> EditFits:
    """Call it before `run`: erasing can drop the fonts it measures with."""
    fits = {
        edited_span.span.id: _fit_of(
            engine, edited_span, room_pt=resolved.rooms[edited_span.span.id]
        )
        for edited_span in resolved.span_edits
    }
    return EditFits(
        replaces={span_id: fit for span_id, fit in fits.items() if fit is not None},
        lines={
            edited_line.spans[0].id: _line_fit_of(engine, edited_line)
            for edited_line in resolved.line_edits
        },
        inserts={
            listed.position: insert_fit(engine, listed.insert) for listed in resolved.inserts
        },
    )


def _fit_of(engine: Engine, edited_span: _EditedSpan, *, room_pt: float) -> Fitted | None:
    edit = edited_span.edit
    if isinstance(edit, Replace):
        span = edited_span.span
        return replace_fit(engine, span, edit.text, room_pt=room_pt, strategy=edit.strategy)
    if isinstance(edit, Redact):
        return None
    assert_never(edit)


def _line_fit_of(engine: Engine, edited_line: _EditedLine) -> LineFitted:
    edit = edited_line.edit
    if isinstance(edit, ReplaceRuns):
        texts = [run.text for run in edit.runs]
        return line_fit(
            engine,
            edited_line.spans,
            texts,
            room_pt=edited_line.room_pt,
            strategy=edit.strategy,
        )
    assert_never(edit)


def plan(
    engine: Engine,
    resolved: Resolved,
    fits: EditFits,
    *,
    strips: Mapping[int, list[Rect]] | None = None,
) -> list[Step]:
    """A page with a redaction shown runs all of them: the check's is export's."""
    shown_in_strips = [
        edited_span
        for edited_span in resolved.span_edits
        if _in_strips(strips, edited_span.span)
    ]
    checked = {
        edited.span.page for edited in shown_in_strips if isinstance(edited.edit, Redact)
    }
    shown: list[_EditedSpan | _EditedLine] = [
        edited_span
        for edited_span in resolved.span_edits
        if _in_strips(strips, edited_span.span) or edited_span.span.page in checked
    ]
    shown += [
        edited_line
        for edited_line in resolved.line_edits
        if any(_in_strips(strips, span) for span in edited_line.spans)
        or edited_line.spans[0].page in checked
    ]
    placed = [
        listed for listed in resolved.inserts if strips is None or listed.insert.page in strips
    ]
    turns_ccw = new_text_turns(engine, resolved) if placed else []
    return [
        *(_step_for(edited, fits, redacted=resolved.redacted) for edited in shown),
        *(
            _Place(
                listed.position,
                insert_span(engine, listed.insert),
                fits.inserts[listed.position],
                turn_ccw=turns_ccw[listed.insert.page],
            )
            for listed in placed
        ),
    ]


def _in_strips(strips: Mapping[int, list[Rect]] | None, span: Span) -> bool:
    return strips is None or _shows(strips, span)


def _shows(strips: Mapping[int, list[Rect]], span: Span) -> bool:
    on_page = strips.get(span.page, [])
    # Turned text is redrawn level, so its box says little of where: anywhere on a drawn page.
    if on_page and span.turned:
        return True
    # Letters drawn at the span's size can reach a little past its box: an accent, a tail.
    reach = span.size * constants.REDRAW_REACH_EM
    top, bottom = span.bbox.y0 - reach, span.bbox.y1 + reach
    return any(strip.y0 < bottom and top < strip.y1 for strip in on_page)


def _step_for(
    edited: _EditedSpan | _EditedLine, fits: EditFits, *, redacted: Mapping[str, Redacted]
) -> Step:
    if isinstance(edited, _EditedLine):
        return _line_step_for(edited, fits)
    edit = edited.edit
    span = edited.span
    if isinstance(edit, Redact):
        return _Erase(redacted[span.id])
    if isinstance(edit, Replace):
        return _Redraw(span, edit.text, fits.replaces[span.id])
    assert_never(edit)


def _line_step_for(edited_line: _EditedLine, fits: EditFits) -> Step:
    edit = edited_line.edit
    if isinstance(edit, ReplaceRuns):
        return _RedrawRuns(fits.lines[edited_line.spans[0].id])
    assert_never(edit)


@dataclass(frozen=True, slots=True)
class Ran:
    notices: list[Notice]
    redrawn: dict[str, Face | None]
    placed: dict[int, Face | None]


def run(engine: Engine, steps: Sequence[Step]) -> Ran:
    """Every erase before any draw: PyMuPDF erases a page at a time, new text included."""
    erased = [span for step in steps for span in _erased_by(step)]
    drawn = [line for step in steps for line in _drawn_by(step)]
    # Old text the erase couldn't clear, as a form field's: the field draws it, not the page.
    stuck = {span.id for span in engine.remove(erased, then_drawn=drawn)}
    redacted = redacted_by(steps)
    runs = runs_of(redacted)
    # A redaction's links (a mailto: can carry its words) and hidden copies go too.
    engine.unlink(runs, lines=[each.span for each in redacted])
    engine.drop_hidden_copies(runs)
    finished = [_finish_step(engine, step, stuck=stuck) for step in steps]
    return Ran(
        [notice for ran in finished for notice in ran.notices],
        redrawn={span_id: face for ran in finished for span_id, face in ran.redrawn.items()},
        placed={position: face for ran in finished for position, face in ran.placed.items()},
    )


def edited_by(steps: Sequence[Step]) -> dict[int, EditedPage]:
    erased = [span for step in steps for span in _erased_by(step)]
    lines = [line for step in steps for line in _lines_drawn_by(step)]
    pages = {span.page for span in erased} | {page for page, _line in lines}
    return {
        page: EditedPage(
            [span.bbox for span in erased if span.page == page],
            drawn=[line for on, line in lines if on == page],
        )
        for page in pages
    }


def _lines_drawn_by(step: Step) -> list[tuple[int, DrawnLine]]:
    if isinstance(step, _Erase):
        return []
    if isinstance(step, _Redraw):
        return [(step.span.page, DrawnLine(step.text, step.span.origin, turn_ccw=0))]
    # Each run on its own baseline, which a line's spans can differ in by a little.
    if isinstance(step, _RedrawRuns):
        return [
            (run.span.page, DrawnLine(run.text, _origin_of(run), turn_ccw=0))
            for run in step.fitted.redrawn
        ]
    if isinstance(step, _Place):
        line = DrawnLine(step.span.text, step.span.origin, turn_ccw=step.turn_ccw)
        return [(step.span.page, line)]
    assert_never(step)


def _origin_of(run: RunFitted) -> tuple[float, float]:
    _x, baseline = run.span.origin
    return run.x, baseline


def redacted_by(steps: Sequence[Step]) -> list[Redacted]:
    return [step.redacted for step in steps if isinstance(step, _Erase)]


def _erased_by(step: Step) -> list[Span]:
    if isinstance(step, _Erase):
        return list(step.redacted.runs)
    if isinstance(step, _Redraw):
        return [step.span]
    if isinstance(step, _RedrawRuns):
        return [run.span for run in step.fitted.redrawn]
    if isinstance(step, _Place):
        return []
    assert_never(step)


def _drawn_by(step: Step) -> list[LineToDraw]:
    if isinstance(step, _Erase):
        return []
    if isinstance(step, _Redraw):
        return [LineToDraw(step.span, step.text, step.fitted.plan)]
    if isinstance(step, _RedrawRuns):
        return [LineToDraw(run.span, run.text, run.plan) for run in step.fitted.redrawn]
    if isinstance(step, _Place):
        return [LineToDraw(step.span, step.span.text, step.fitted.plan)]
    assert_never(step)


def _finish_step(engine: Engine, step: Step, *, stuck: set[str]) -> Ran:
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
    # A run the erase couldn't clear: the line goes back as it was, rather than half moved.
    if isinstance(step, _RedrawRuns) and any(
        run.span.id in stuck for run in step.fitted.redrawn
    ):
        return _put_back(engine, step.fitted, stuck=stuck)
    if isinstance(step, _RedrawRuns):
        return _draw_runs(engine, step.fitted)
    if isinstance(step, _Place):
        drawn = engine.draw(
            step.span, step.span.text, plan=step.fitted.plan, turn_ccw=step.turn_ccw
        )
        asked = drawn.face is not None and drawn.face.name == step.span.font
        face = None if asked else drawn.face
        insert_notices: list[Notice] = [
            InsertNotice(step.position, said) for said in drawn.notices
        ]
        return Ran(insert_notices, redrawn={}, placed={step.position: face})
    assert_never(step)


def _draw_runs(engine: Engine, fitted: LineFitted) -> Ran:
    notices: list[Notice] = []
    redrawn: dict[str, Face | None] = {}
    for run in fitted.redrawn:
        # Only its start moves: its box and pieces stay the page's, for anything read of them.
        moved_span = replace(run.span, origin=_origin_of(run))
        drawn = engine.draw(
            moved_span, run.text, plan=run.plan, size=run.size, scale_x=run.scale_x
        )
        notices += [SpanNotice(run.span.id, said) for said in drawn.notices]
        redrawn[run.span.id] = drawn.face
    return Ran(notices, redrawn=redrawn, placed={})


def _put_back(engine: Engine, fitted: LineFitted, *, stuck: set[str]) -> Ran:
    for run in fitted.redrawn:
        if run.span.id not in stuck:
            engine.draw(run.span, run.span.text, plan=run.put_back)
    notices: list[Notice] = [
        SpanNotice(run.span.id, Message("line_not_edited"))
        for run in fitted.redrawn
        if run.span.id in stuck
    ]
    return Ran(notices, redrawn={}, placed={})


@dataclass(frozen=True, slots=True, eq=False)
class Planned:
    fits: EditFits
    steps: list[Step]


def planned_of(engine: Engine, resolved: Resolved) -> Planned:
    fits = fits_of(engine, resolved)
    return Planned(fits, plan(engine, resolved, fits))


def apply_edits(engine: Engine, resolved: Resolved, planned: Planned) -> Applied:
    fits = planned.fits
    rewritten = _rewritten(resolved)
    # Before run: erasing can drop the fonts a font's name is read from.
    shown_names = {span.id: engine.shown_name(span) for span in rewritten}
    ran = run(engine, planned.steps)
    verdicts = verdicts_of(fits, ran)
    replaced = [
        Replaced(verdicts.replaces[span.id], shown_names[span.id])
        for span in rewritten
        if span.id in ran.redrawn
    ]
    return Applied(resolved.skipped, ran.notices, replaced)


def _rewritten(resolved: Resolved) -> list[Span]:
    """One that types a span's own words back draws it as it was: no edit to count."""
    replaced = [
        edited_span.span
        for edited_span in resolved.span_edits
        if isinstance(edited_span.edit, Replace)
        and edited_span.edit.text != edited_span.span.text
    ]
    in_lines = [
        span
        for edited_line in resolved.line_edits
        for span, run in zip(edited_line.spans, edited_line.edit.runs, strict=True)
        if run.text != span.text
    ]
    return replaced + in_lines


@dataclass(frozen=True, slots=True)
class Verdicts:
    replaces: dict[str, Verdict]
    inserts: dict[int, Verdict]


def verdicts_of(fits: EditFits, ran: Ran) -> Verdicts:
    """The one way render and export judge an edit, so the note and the export's log agree."""
    in_lines = {
        run.span.id: _as_drawn(run.verdict, ran.redrawn.get(run.span.id))
        for fitted in fits.lines.values()
        for run in fitted.redrawn
    }
    return Verdicts(
        replaces={
            span_id: _as_drawn(fitted.verdict, ran.redrawn.get(span_id))
            for span_id, fitted in fits.replaces.items()
        }
        | in_lines,
        inserts={
            position: _as_drawn(fitted.verdict, ran.placed.get(position))
            for position, fitted in fits.inserts.items()
        },
    )


def _as_drawn(planned: Verdict, face: Face | None) -> Verdict:
    return planned if face is None else replace(planned, fidelity="substitute", why=None)
