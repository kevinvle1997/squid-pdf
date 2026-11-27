"""Turn an edit list into engine calls, in named steps.

`core` knows no Replace, so it imports no feature. The steps, in order: `resolve`,
`fits_of`, `plan`, then `run`.
"""

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
    RunText,
    SpanEdit,
)
from squidpdf.editing.errors import BadReference, RedactionConflict
from squidpdf.editing.fit import (
    EditFits,
    Fitted,
    LineFitted,
    LineRedraw,
    RunFitted,
    Verdict,
    insert_fit,
    insert_span,
    kept_run,
    line_fit,
    replace_fit,
    run_key,
)
from squidpdf.editing.redacted_words import merged_ranges, range_of, redacted_of, runs_of
from squidpdf.editing.split_places import ink_after, part_of, uncovered
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
    """A span's last edit in the list, with the span it points at."""

    edit: SpanEdit
    span: Span


@dataclass(frozen=True, slots=True, eq=False)
class _EditedLine:
    """A line edit still in force: what it covers of which spans, and its line's room."""

    position: int
    edit: ReplaceRuns
    spans: tuple[Span, ...]  # those it covers some of, left to right
    covered: dict[str, range]  # the code points it takes of each, by id
    line: tuple[Span, ...]  # every span of the line they're on, left to right
    room_pt: float  # the line's, or the lone span's

    @property
    def line_ids(self) -> tuple[str, ...]:
        """What tells its line from the others: its spans' ids."""
        return tuple(span.id for span in self.line)

    def overlaps(self, other: _EditedLine) -> bool:
        """Whether both take some of the same text, so the later one is the last word."""
        return any(
            _ranges_meet(taken, other.covered[span_id])
            for span_id, taken in self.covered.items()
            if span_id in other.covered
        )


def _ranges_meet(a: range, b: range) -> bool:
    """Whether two ranges of a text share a code point."""
    return a.start < b.stop and b.start < a.stop


@dataclass(frozen=True, slots=True, eq=False)
class _CutLine:
    """A line edit placed on the page: each part it covers, and what stops it at a split."""

    edited: _EditedLine
    parts: tuple[Span, ...]  # each span it covers some of, cut to that part
    next_x: float | None  # where the text after a split starts; None at the line's end

    @property
    def key(self) -> str:
        """What its fit is kept by: its first run's."""
        return run_key(self.parts[0].id, self.edited.covered[self.parts[0].id].start)


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
    # Each line edit still in force, none overlapping another, placed on the page.
    line_edits: list[_CutLine]
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
    # What each redacted span loses, worked out once from its page, by id, in edit order.
    redacted: dict[str, Redacted]


@dataclass(frozen=True, slots=True)
class _Erase:
    """A redaction: the words it takes of a span, and their links, go; nothing is drawn."""

    redacted: Redacted


@dataclass(frozen=True, slots=True, eq=False)
class _Redraw:
    """A replace: the span's text goes, and `text` is drawn in its place, as its fit says."""

    span: Span
    text: str
    fitted: Fitted


@dataclass(frozen=True, slots=True, eq=False)
class _RedrawRuns:
    """A line's edits: its spans changed or moved go, and each part is drawn where it fits."""

    redraw: LineRedraw


@dataclass(frozen=True, slots=True, eq=False)
class _Place:
    """An insert: new text drawn where there was none, as its fit says, turned with its page."""

    position: int
    span: Span
    fitted: Fitted
    turn_ccw: QuarterTurn


# What happens to the page for one edit.
type Step = _Erase | _Redraw | _RedrawRuns | _Place


def resolve(
    engine: Engine, index: SpanIndex, *, free_space: FreeSpace, edits: Sequence[Edit]
) -> Resolved:
    """The list checked against the document: each edit with what it points at.

    A redaction of nothing raises BadReference, of words its text lacks BadRange, and a replace
    after a redaction of the same text RedactionConflict: the order can't undo one. A span
    edited twice is drawn once, as its last edit leaves it; its redactions take words together.
    `free_space` is what the caller read from the original: each span's room, and its lines.
    """
    page_count = engine.page_count()
    listed = _EditList(
        page_count,
        spans=_SpanEdits(latest={}, redacted_ranges={}, lines=[]),
        pages=_PageOps(order=list(range(page_count)), turns={}),
        inserts=[],
        lines={span_id: line for line in free_space.lines for span_id in line.span_ids},
        rooms=free_space.rooms,
    )
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        left_out = listed.read(edit, index=index, position=position)
        skipped += [Skipped(position, "bad_reference", why) for why in left_out]
    span_edits = list(listed.spans.latest.values())
    cuts = [(edited, _cut_of(engine, edited)) for edited in listed.spans.lines]
    skipped += [
        Skipped(edited.position, "bad_reference", Message("split_unreadable"))
        for edited, cut in cuts
        if cut is None
    ]
    rooms = free_space.rooms
    return Resolved(
        span_edits,
        line_edits=[cut for _edited, cut in cuts if cut is not None],
        inserts=listed.inserts,
        skipped=skipped,
        # Of the rooms the caller read from the original, only the edited spans'.
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
    """The edit list read so far, by kind: span edits, inserts and page ops."""

    page_count: int
    spans: _SpanEdits
    pages: _PageOps
    inserts: list[_ListedInsert]
    lines: dict[str, Line]  # the document's lines, by each of their spans' ids
    rooms: Mapping[str, float]  # each span's room, which a span on no line is edited in

    def read(self, edit: Edit, *, index: SpanIndex, position: int) -> list[Message]:
        """Read one more edit; returns why it's left out, if it is."""
        # Folded into the pages kept, in list order.
        if isinstance(edit, PageOp) and _names_pages(edit, self.page_count):
            return self.pages.fold(edit)
        # A page op naming a page it doesn't have.
        if isinstance(edit, PageOp):
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
            return self.spans.add(edit, index.by_id.get(edit.span_id), position=position)
        # An edit of a line: the last of the text it covers, if its spans are one line's.
        if isinstance(edit, ReplaceRuns):
            edited_line = self._edited_line(edit, index=index, position=position)
            if edited_line is None:
                return [Message("not_one_line")]
            return self.spans.add_line(edited_line, position=position)
        assert_never(edit)

    def _edited_line(
        self, edit: ReplaceRuns, *, index: SpanIndex, position: int
    ) -> _EditedLine | None:
        """`edit` with what it covers; None unless its spans are consecutive on one line.

        Raises InvalidRequest for a start or end its span's text doesn't have.
        """
        ids = tuple(run.span_id for run in edit.runs)
        # .get: the browser can send a span id this document doesn't have.
        first = index.by_id.get(ids[0]) if ids else None
        if first is None:
            return None
        # .get: a span on no line is edited alone, in its own room.
        line = self.lines.get(first.id)
        line_ids = (first.id,) if line is None else line.span_ids
        at = line_ids.index(first.id)
        if line_ids[at : at + len(ids)] != ids:
            return None
        spans = tuple(index.by_id[span_id] for span_id in ids)
        covered = {
            span.id: _covered_of(run, span, position=position)
            for span, run in zip(spans, edit.runs, strict=True)
        }
        return _EditedLine(
            position,
            edit,
            spans,
            covered,
            line=tuple(index.by_id[span_id] for span_id in line_ids),
            room_pt=self.rooms[first.id] if line is None else line.room_pt,
        )


@dataclass(slots=True)
class _SpanEdits:
    """The span edits read so far: each span's last, and what its redactions take, by id.

    In the order first edited.
    """

    latest: dict[str, _EditedSpan]
    # The code points each span's redactions so far take, merged, by id, in edit order.
    redacted_ranges: dict[str, tuple[range, ...]]
    # The line edits still in force, none overlapping another, in the order made.
    lines: list[_EditedLine]

    def add(self, edit: SpanEdit, span: Span | None, *, position: int) -> list[Message]:
        """Read one more edit of a span; returns why it's left out, if it is."""
        # Skipping it would be a leak.
        if isinstance(edit, Redact) and span is None:
            raise BadReference(edit.span_id)
        # A replace of text it doesn't have.
        if span is None:
            return [Message("no_span")]
        # A redaction of text the document has, with the span's others: nothing undoes it.
        if isinstance(edit, Redact):
            # .get: the span's first redaction.
            earlier = self.redacted_ranges.get(edit.span_id, ())
            ranges = merged_ranges([*earlier, range_of(edit, span)])
            self.redacted_ranges[edit.span_id] = ranges
            self._forget_lines_of(edit.span_id)
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        # A replace of redacted text: the browser undoes a redaction by leaving it out.
        if self._redacted(edit.span_id):
            raise RedactionConflict(
                debug=f"edit {position} replaces span {edit.span_id}, redacted before"
            )
        # A replace of text the document has. Overwriting a key keeps its first place.
        if isinstance(edit, Replace):
            self._forget_lines_of(edit.span_id)
            self.latest[edit.span_id] = _EditedSpan(edit, span)
            return []
        assert_never(edit)

    def add_line(self, edited_line: _EditedLine, *, position: int) -> list[Message]:
        """Read one more line edit: the last of the text it covers, what it overlaps gone."""
        redacted = [span.id for span in edited_line.spans if self._redacted(span.id)]
        # A line's edit over redacted text: the browser undoes a redaction by leaving it out.
        if redacted:
            raise RedactionConflict(
                debug=f"edit {position} replaces span {redacted[0]}, redacted before"
            )
        self.lines = [earlier for earlier in self.lines if not earlier.overlaps(edited_line)]
        for span in edited_line.spans:
            # .pop with a default: a span not edited alone before.
            self.latest.pop(span.id, None)
        self.lines.append(edited_line)
        return []

    def _forget_lines_of(self, span_id: str) -> None:
        """Forget every line edit taking some of `span_id`, each whole.

        A line edit is drawn whole or not at all: a later edit of one of its spans takes it.
        """
        self.lines = [edited for edited in self.lines if span_id not in edited.covered]

    def _redacted(self, span_id: str) -> bool:
        """Whether the span's last edit so far is a redaction: none after it can replace it."""
        # .get: a span not edited yet.
        last = self.latest.get(span_id)
        return last is not None and isinstance(last.edit, Redact)


def _covered_of(run: RunText, span: Span, *, position: int) -> range:
    """The code points of `span`'s text `run` takes; InvalidRequest where it has none."""
    start = run.start or 0
    end = len(span.text) if run.end is None else run.end
    if not start < end <= len(span.text):
        debug = f"edit {position}: [{start}, {end}) of a text {len(span.text)} long"
        raise InvalidRequest(debug=debug)
    return range(start, end)


def _cut_of(engine: Engine, edited_line: _EditedLine) -> _CutLine | None:
    """A line edit placed on the page; None where a split's letters can't be told apart.

    Ask before the erase: it reads the page.
    """
    parts = [part_of(engine, span, edited_line.covered[span.id]) for span in edited_line.spans]
    found = tuple(part for part in parts if part is not None)
    if len(found) < len(parts):
        return None
    return _CutLine(edited_line, found, next_x=_next_x(engine, edited_line))


def _next_x(engine: Engine, edited_line: _EditedLine) -> float | None:
    """Where the text after a line edit starts: its last span's rest, else the span after."""
    last = edited_line.spans[-1]
    end = edited_line.covered[last.id].stop
    in_span = ink_after(engine, last, end)
    if in_span is not None:
        return in_span
    following = edited_line.line[edited_line.line.index(last) + 1 :]
    if not following:
        return None
    x, _baseline = following[0].origin
    return x


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
            # .get: the page's first turn.
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


def redacted_in(resolved: Resolved) -> tuple[Redacted, ...]:
    """What each span whose last edit is a redaction loses, numbered as in the original."""
    return tuple(resolved.redacted.values())


def new_text_turns(engine: Engine, resolved: Resolved) -> list[QuarterTurn]:
    """How far new text is turned on each page, so it reads upright as the page is shown.

    Turned back as far as its page turns, an edit's turn included.
    """
    # .get: most pages have no turn among the edits.
    return [
        turn_added(page.turn_cw, resolved.turns.get(number, 0))
        for number, page in enumerate(engine.pages())
    ]


def fits_of(engine: Engine, resolved: Resolved) -> EditFits:
    """A fit, with its plan, for each replace, line edit and insert left, drawn or not.

    Measurement only. Call it before `run`: erasing can drop the fonts it measures with.
    """
    fits = {
        edited_span.span.id: _fit_of(
            engine, edited_span, room_pt=resolved.rooms[edited_span.span.id]
        )
        for edited_span in resolved.span_edits
    }
    lines = {cut.key: _line_fit_of(engine, cut) for cut in resolved.line_edits}
    return EditFits(
        replaces={span_id: fit for span_id, fit in fits.items() if fit is not None},
        lines=lines,
        redraws=_redraws_of(engine, resolved.line_edits, lines),
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


def _line_fit_of(engine: Engine, cut: _CutLine) -> LineFitted:
    """What a line's edit will look like, run by run, in its room or up to its split."""
    edit = cut.edited.edit
    if isinstance(edit, ReplaceRuns):
        keys = [run_key(part.id, cut.edited.covered[part.id].start) for part in cut.parts]
        return line_fit(
            engine,
            cut.parts,
            [run.text for run in edit.runs],
            keys=keys,
            room_pt=cut.edited.room_pt,
            next_x=cut.next_x,
            strategy=edit.strategy,
        )
    assert_never(edit)


def _redraws_of(
    engine: Engine, cuts: Sequence[_CutLine], lines: Mapping[str, LineFitted]
) -> dict[tuple[str, ...], LineRedraw]:
    """What each edited line erases and draws back, by its span ids: its edits together."""
    by_line: dict[tuple[str, ...], list[_CutLine]] = {}
    for cut in cuts:
        by_line.setdefault(cut.edited.line_ids, []).append(cut)
    return {
        line_ids: _redraw_of(engine, on_line, lines) for line_ids, on_line in by_line.items()
    }


def _redraw_of(
    engine: Engine, cuts: list[_CutLine], lines: Mapping[str, LineFitted]
) -> LineRedraw:
    """One line's edits together: a span with any part changed or moved is drawn again whole.

    Each part is drawn where its own edit puts it, and any part no edit takes where it was.
    """
    runs = [run for cut in cuts for run in lines[cut.key].runs]
    erased_ids = {run.span.id for run in runs if run.changed or run.moved}
    erased = [span for span in cuts[0].edited.line if span.id in erased_ids]
    kept = [run for span in erased for run in _kept_parts(engine, span, cuts)]
    drawn = [run for run in [*runs, *kept] if run.span.id in erased_ids]
    return LineRedraw(
        erased,
        sorted(drawn, key=lambda run: run.x),
        put_back={span.id: engine.fit.plan_for(span, span.text) for span in erased},
    )


def _kept_parts(engine: Engine, span: Span, cuts: list[_CutLine]) -> list[RunFitted]:
    """The parts of `span` no edit takes, as runs drawn where they are."""
    covered = [cut.edited.covered[span.id] for cut in cuts if span.id in cut.edited.covered]
    parts = [
        (taken, part_of(engine, span, taken)) for taken in uncovered(len(span.text), covered)
    ]
    # Each starts and ends where an edit's part does, which was found, so each is too.
    return [
        kept_run(engine, part, key=run_key(span.id, taken.start))
        for taken, part in parts
        if part is not None
    ]


def plan(
    engine: Engine,
    resolved: Resolved,
    fits: EditFits,
    *,
    strips: Mapping[int, list[Rect]] | None = None,
) -> list[Step]:
    """The steps the edits shown take, each drawn with its fit's plan.

    `strips` are the rows drawn, by page, or None for every edit. An edit of text shows where
    one of its spans' rows meets a strip; turned text, redrawn level, and inserts anywhere on
    a drawn page. A page with a redaction shown runs all of them: the check's is export's.
    """
    shown_in_strips = [
        edited_span
        for edited_span in resolved.span_edits
        if _in_strips(strips, edited_span.span)
    ]
    checked = {
        edited.span.page for edited in shown_in_strips if isinstance(edited.edit, Redact)
    }
    shown = [
        edited_span
        for edited_span in resolved.span_edits
        if _in_strips(strips, edited_span.span) or edited_span.span.page in checked
    ]
    # A line's edits are drawn together, so once for the line.
    shown_lines = {
        cut.edited.line_ids: cut
        for cut in resolved.line_edits
        if any(_in_strips(strips, span) for span in cut.edited.line)
        or cut.edited.line[0].page in checked
    }
    placed = [
        listed for listed in resolved.inserts if strips is None or listed.insert.page in strips
    ]
    turns_ccw = new_text_turns(engine, resolved) if placed else []
    return [
        *(_step_for(edited, fits, redacted=resolved.redacted) for edited in shown),
        *(_line_step_for(cut, fits) for cut in shown_lines.values()),
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
    """Whether an edit to `span` shows in what's drawn: anywhere, when `strips` is None."""
    return strips is None or _shows(strips, span)


def _shows(strips: Mapping[int, list[Rect]], span: Span) -> bool:
    """Whether an edit to `span` shows in the strips drawn on its page."""
    # .get: a page with no strip drawn on it shows no edit.
    on_page = strips.get(span.page, [])
    # Turned text is redrawn level, so its box says little of where: anywhere on a drawn page.
    if on_page and span.turned:
        return True
    # Letters drawn at the span's size can reach a little past its box: an accent, a tail.
    reach = span.size * constants.REDRAW_REACH_EM
    top, bottom = span.bbox.y0 - reach, span.bbox.y1 + reach
    return any(strip.y0 < bottom and top < strip.y1 for strip in on_page)


def _step_for(edited: _EditedSpan, fits: EditFits, *, redacted: Mapping[str, Redacted]) -> Step:
    """What one edit of a span does to the page; `redacted` is what each redaction takes."""
    edit = edited.edit
    span = edited.span
    if isinstance(edit, Redact):
        return _Erase(redacted[span.id])
    if isinstance(edit, Replace):
        return _Redraw(span, edit.text, fits.replaces[span.id])
    assert_never(edit)


def _line_step_for(cut: _CutLine, fits: EditFits) -> Step:
    """What the edits of one line do to the page, `cut` one of them."""
    edit = cut.edited.edit
    if isinstance(edit, ReplaceRuns):
        return _RedrawRuns(fits.redraws[cut.edited.line_ids])
    assert_never(edit)


@dataclass(frozen=True, slots=True)
class Ran:
    """What doing the steps came out with: what came out other than asked, and each draw.

    `redrawn` is each replace by span id, and each part of a line drawn by its key; `placed`
    each insert, by its place: the face we ship that drew it, None for its own font (new
    text's face, where it draws). An edit the erase couldn't clear isn't drawn, so isn't there.
    """

    notices: list[Notice]
    redrawn: dict[str, Face | None]
    placed: dict[int, Face | None]


def run(engine: Engine, steps: Sequence[Step]) -> Ran:
    """Do the steps; returns what came out other than asked, and what drew each edit.

    Every erase happens in one pass before any draw: PyMuPDF erases a page at a
    time, and an erase after a redraw would take the new text too.
    """
    erased = [span for step in steps for span in _erased_by(step)]
    drawn = [line for step in steps for line in _drawn_by(step)]
    # Old text the erase couldn't clear, as a form field's: the field draws it, not the page.
    # Only a redraw reads it: a redaction's is checked where it's said, by RedactionController.
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
    """What the steps erase and draw, by page: what the redaction check allows for."""
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
    """What a step draws, a line a run, and each one's page: none for one that only erases."""
    if isinstance(step, _Erase):
        return []
    if isinstance(step, _Redraw):
        return [(step.span.page, DrawnLine(step.text, step.span.origin, turn_ccw=0))]
    # Each run on its own baseline, which a line's spans can differ in by a little.
    if isinstance(step, _RedrawRuns):
        return [
            (run.span.page, DrawnLine(run.text, _origin_of(run), turn_ccw=0))
            for run in step.redraw.drawn
        ]
    if isinstance(step, _Place):
        line = DrawnLine(step.span.text, step.span.origin, turn_ccw=step.turn_ccw)
        return [(step.span.page, line)]
    assert_never(step)


def _origin_of(run: RunFitted) -> tuple[float, float]:
    """Where a line's run is drawn: where the runs before it leave it, on its own baseline."""
    _x, baseline = run.span.origin
    return run.x, baseline


def redacted_by(steps: Sequence[Step]) -> list[Redacted]:
    """What the steps redact, span by span, in order."""
    return [step.redacted for step in steps if isinstance(step, _Erase)]


def _erased_by(step: Step) -> list[Span]:
    """The text a step erases first, as spans: none for one that only draws."""
    if isinstance(step, _Erase):
        return list(step.redacted.runs)
    if isinstance(step, _Redraw):
        return [step.span]
    # Each span whole: a moved run's old place, and the parts a split keeps, are cleared too.
    if isinstance(step, _RedrawRuns):
        return list(step.redraw.erased)
    # An insert: there was no text there.
    if isinstance(step, _Place):
        return []
    assert_never(step)


def _drawn_by(step: Step) -> list[LineToDraw]:
    """What a step draws once the erasing is done: none for one that only erases."""
    if isinstance(step, _Erase):
        return []
    if isinstance(step, _Redraw):
        return [LineToDraw(step.span, step.text, step.fitted.plan)]
    if isinstance(step, _RedrawRuns):
        return [LineToDraw(run.span, run.text, run.plan) for run in step.redraw.drawn]
    if isinstance(step, _Place):
        return [LineToDraw(step.span, step.span.text, step.fitted.plan)]
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
    # A run the erase couldn't clear: the line goes back as it was, rather than half moved.
    if isinstance(step, _RedrawRuns) and any(span.id in stuck for span in step.redraw.erased):
        return _put_back(engine, step.redraw, stuck=stuck)
    if isinstance(step, _RedrawRuns):
        return _draw_runs(engine, step.redraw)
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


def _draw_runs(engine: Engine, redraw: LineRedraw) -> Ran:
    """Draw each part of a line's spans erased, where its fit puts it."""
    notices: list[Notice] = []
    redrawn: dict[str, Face | None] = {}
    for run in redraw.drawn:
        # Only its start moves: its box and pieces stay the page's, for anything read of them.
        moved_span = replace(run.span, origin=_origin_of(run))
        drawn = engine.draw(
            moved_span, run.text, plan=run.plan, size=run.size, scale_x=run.scale_x
        )
        notices += [SpanNotice(run.span.id, said) for said in drawn.notices]
        redrawn[run.key] = drawn.face
    return Ran(notices, redrawn=redrawn, placed={})


def _put_back(engine: Engine, redraw: LineRedraw, *, stuck: set[str]) -> Ran:
    """Draw back as it was each span of a line the erase cleared, since one it couldn't."""
    for span in redraw.erased:
        if span.id not in stuck:
            engine.draw(span, span.text, plan=redraw.put_back[span.id])
    notices: list[Notice] = [
        SpanNotice(span.id, Message("line_not_edited"))
        for span in redraw.erased
        if span.id in stuck
    ]
    return Ran(notices, redrawn={}, placed={})


@dataclass(frozen=True, slots=True, eq=False)
class Planned:
    """Every edit's fit, and the steps they take on every page: worked out once, then run."""

    fits: EditFits
    steps: list[Step]


def planned_of(engine: Engine, resolved: Resolved) -> Planned:
    """The fits and the steps of every edit. Before any is run: erasing can drop fonts."""
    fits = fits_of(engine, resolved)
    return Planned(fits, plan(engine, resolved, fits))


def apply_edits(engine: Engine, resolved: Resolved, planned: Planned) -> Applied:
    """Apply every edit on every page, as `planned`, in memory. Nothing is written."""
    fits = planned.fits
    rewritten = _rewritten(resolved)
    # Before run: erasing can drop the fonts a font's name is read from.
    shown_names = {span.id: engine.shown_name(span) for _key, span in rewritten}
    ran = run(engine, planned.steps)
    verdicts = verdicts_of(fits, ran)
    replaced = [
        Replaced(verdicts.replaces[key], shown_names[span.id])
        for key, span in rewritten
        if key in ran.redrawn
    ]
    return Applied(resolved.skipped, ran.notices, replaced)


def _rewritten(resolved: Resolved) -> list[tuple[str, Span]]:
    """Each span, or part of one, whose last edit replaces its words with others, by its key.

    One that types a span's own words back draws it as it was, or only moves it: no edit to
    count.
    """
    replaced = [
        (edited_span.span.id, edited_span.span)
        for edited_span in resolved.span_edits
        if isinstance(edited_span.edit, Replace)
        and edited_span.edit.text != edited_span.span.text
    ]
    in_lines = [
        (run_key(part.id, cut.edited.covered[part.id].start), part)
        for cut in resolved.line_edits
        for part, run in zip(cut.parts, cut.edited.edit.runs, strict=True)
        if run.text != part.text
    ]
    return replaced + in_lines


@dataclass(frozen=True, slots=True)
class Verdicts:
    """How each edit came out: a replace by span id, a line's run by key, an insert by place."""

    replaces: dict[str, Verdict]
    inserts: dict[int, Verdict]


def verdicts_of(fits: EditFits, ran: Ran) -> Verdicts:
    """How each edit came out: as drawn where `run` drew it, else as planned.

    The one way render and export judge an edit, so the note and the export's log agree.
    """
    # .get: an edit on a page this run didn't draw has only its plan to go by.
    in_lines = {
        run.key: _as_drawn(run.verdict, ran.redrawn.get(run.key))
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
    """An edit as it was drawn: a face that drew it, the page having refused the font, too."""
    return planned if face is None else replace(planned, fidelity="substitute", why=None)
