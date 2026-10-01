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

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import assert_never

from squidpdf.core import Message
from squidpdf.core.engine import Engine
from squidpdf.core.fonts import FACES
from squidpdf.core.types import Span, SpanIndex, new_text
from squidpdf.editing.edits import Edit, Insert, Redact, Replace, SpanEdit
from squidpdf.editing.errors import BadReference, RedactionConflict
from squidpdf.editing.fit import FitReport, LogFits, Option, options_for
from squidpdf.editing.types import Applied, Notice, Skipped, Strategy

__all__ = [
    "Resolved",
    "Erase",
    "Step",
    "resolve",
    "log_fits",
    "plan",
    "run",
    "apply_edits",
    "insert_fit",
    "replace_fit",
]


@dataclass(frozen=True, slots=True)
class EditedSpan:
    """A span's last edit in the list, with the span it points at."""

    edit: SpanEdit
    span: Span


@dataclass(frozen=True, slots=True)
class ListedInsert:
    """An insert on a page the document has, with its place in the list the browser sent."""

    position: int
    insert: Insert


@dataclass(frozen=True, slots=True)
class Resolved:
    """An edit list checked against the document: what each edit points at."""

    # The last edit to each span, in the order spans were first edited.
    span_edits: list[EditedSpan]
    inserts: list[ListedInsert]
    # Edits that point at nothing, left out and said why.
    skipped: list[Skipped]
    # How many pages the original has, numbered from 0.
    page_count: int


@dataclass(frozen=True, slots=True)
class Erase:
    """A redaction: the span's text and its links go, and nothing is drawn in their place."""

    span: Span


@dataclass(frozen=True, slots=True)
class Redraw:
    """A replace: the span's text goes, and `text` is drawn in its place.

    A None `size` keeps the span's own; `scale_x` narrows the line from its start.
    """

    span: Span
    text: str
    size: float | None
    scale_x: float


@dataclass(frozen=True, slots=True)
class Place:
    """An insert: new text drawn where there was none, turned as its page is turned."""

    position: int
    span: Span
    turn: int


# What happens to the page for one edit.
type Step = Erase | Redraw | Place


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
    # Overwriting a key keeps its place, so spans stay in the order first edited.
    latest: dict[str, EditedSpan] = {}
    redacted: set[str] = set()
    inserts: list[ListedInsert] = []
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        # .get below: the browser can send a span id this document doesn't have.
        match edit:
            # An insert on a page the document has.
            case Insert(page=page) if 0 <= page < page_count:
                inserts.append(ListedInsert(position, edit))
            # An insert on a page it doesn't have.
            case Insert():
                skipped.append(Skipped(position, "bad_reference", Message("no_page")))
            # A redaction of text the document has: later edits can't bring it back.
            case Redact(span_id=span_id) if (span := index.get(span_id)) is not None:
                redacted.add(span_id)
                latest[span_id] = EditedSpan(edit, span)
            # A redaction of text it doesn't have: skipping it would be a leak.
            case Redact(span_id=span_id):
                raise BadReference(span_id)
            # A replace of redacted text: the browser undoes a redaction by leaving it out.
            case Replace(span_id=span_id) if span_id in redacted:
                raise RedactionConflict(
                    debug=f"edit {position} replaces span {span_id}, redacted before"
                )
            # A replace of text the document has.
            case Replace(span_id=span_id) if (span := index.get(span_id)) is not None:
                latest[span_id] = EditedSpan(edit, span)
            # A replace of text it doesn't have.
            case Replace():
                skipped.append(Skipped(position, "bad_reference", Message("no_span")))
            case _:
                assert_never(edit)
    return Resolved(list(latest.values()), inserts, skipped, page_count)


def log_fits(engine: Engine, resolved: Resolved) -> LogFits:
    """A fit for each span the log leaves replaced and for each insert, drawn or not.

    Measurement only. Call it before `run`: erasing can drop the fonts it measures with.
    """
    fits = {edited.span.id: fit_of(engine, edited) for edited in resolved.span_edits}
    return LogFits(
        replaces={span_id: fit for span_id, fit in fits.items() if fit is not None},
        inserts={
            listed.position: insert_fit(engine, listed.insert) for listed in resolved.inserts
        },
    )


def fit_of(engine: Engine, edited: EditedSpan) -> FitReport | None:
    """What a span edit will look like; None for one that draws nothing to fit."""
    match edited.edit:
        case Replace(text=text, strategy=strategy):
            return replace_fit(engine, edited.span, text, strategy=strategy)
        case Redact():
            return None
        case _:
            assert_never(edited.edit)


def plan(
    engine: Engine, resolved: Resolved, *, pages: Collection[int] | None = None
) -> list[Step]:
    """The steps the edits on `pages` take, worked out before anything is erased.

    Every page's edits when `pages` is None.
    """
    shown = [
        edited for edited in resolved.span_edits if pages is None or edited.span.page in pages
    ]
    placed = [
        listed for listed in resolved.inserts if pages is None or listed.insert.page in pages
    ]
    # New text reads upright as the page is shown: turned by its page's own turn.
    turns = [page.rotation for page in engine.pages()] if placed else []
    return [
        *(step_for(engine, edited) for edited in shown),
        *(
            Place(listed.position, insert_span(listed.insert), turn=turns[listed.insert.page])
            for listed in placed
        ),
    ]


def step_for(engine: Engine, edited: EditedSpan) -> Step:
    """What one span edit does to the page."""
    match edited.edit:
        case Redact():
            return Erase(edited.span)
        case Replace() as replace:
            return redraw_of(engine, edited.span, replace)
        case _:
            assert_never(edited.edit)


def redraw_of(engine: Engine, span: Span, replace: Replace) -> Redraw:
    """A replacement at the size and width it's drawn: its own, or shrunk or condensed to fit.

    The edit's strategy counts only if it was offered, as its fit says.
    """
    original = engine.measure(span, span.text)
    typed = engine.measure(span, replace.text)
    strategy = strategy_drawn(replace.strategy, options_for(typed - original, original))
    match strategy:
        # Drawn as typed: the span's size, no stretch.
        case "as-is":
            return Redraw(span, replace.text, size=None, scale_x=1.0)
        # Smaller letters, same shape: width goes with size, so it ends where the original did.
        case "shrink":
            return Redraw(span, replace.text, size=span.size * original / typed, scale_x=1.0)
        # The same size, letters squeezed narrower, to the same end.
        case "condense":
            return Redraw(span, replace.text, size=None, scale_x=original / typed)
        case _:
            assert_never(strategy)


def strategy_drawn(asked: Strategy, options: list[Option]) -> Strategy:
    """The way out that's drawn: the one asked for if it was offered, else as-is."""
    offered = asked in {option.name for option in options}
    return asked if offered else "as-is"


def run(engine: Engine, steps: Sequence[Step]) -> list[Notice]:
    """Do the steps; returns what came out other than asked.

    Every erase happens in one pass before any draw: PyMuPDF erases a page at a
    time, and an erase after a redraw would take the new text too.
    """
    erased = [span for span in map(erased_by, steps) if span is not None]
    engine.remove(erased)
    # A redaction's links go too, as one can carry the text it's on (a mailto:).
    engine.unlink([step.span for step in steps if isinstance(step, Erase)])
    return [notice for step in steps for notice in finish_step(engine, step)]


def erased_by(step: Step) -> Span | None:
    """The span whose text a step erases first; None for one that only draws."""
    match step:
        case Erase(span=span) | Redraw(span=span):
            return span
        case Place():
            return None
        case _:
            assert_never(step)


def finish_step(engine: Engine, step: Step) -> list[Notice]:
    """Do what a step does once the erasing is done; returns what came out other than asked."""
    match step:
        # A redaction: nothing to draw.
        case Erase():
            return []
        case Redraw(span=span, text=text, size=size, scale_x=scale_x):
            drawn = engine.draw(span, text, size=size, scale_x=scale_x)
            return [Notice(span.id, said) for said in drawn]
        case Place(position=position, span=span, turn=turn):
            drawn = engine.draw(span, span.text, turn=turn)
            return [Notice(None, said, edit=position) for said in drawn]
        case _:
            assert_never(step)


def apply_edits(engine: Engine, resolved: Resolved) -> Applied:
    """Apply every edit on every page, in memory. Nothing is written."""
    return Applied(resolved.skipped, run(engine, plan(engine, resolved)))


def insert_span(insert: Insert) -> Span:
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
    span = insert_span(insert)
    [report] = engine.assess(SpanIndex([span]))
    shipped = insert.font in FACES
    # Not a face we ship, and not a font of this page's we can use: it can't be used at all.
    # One that only lacks a letter can: the stand-in draws that line, as for a replace.
    unusable = not shipped and not report.in_file
    return FitReport(
        delta_pt=0.0,
        missing=[] if unusable else engine.missing(span, insert.text),
        left_out=engine.left_out(span, insert.text),
        stand_in=engine.stand_in(span, insert.text),
        unavailable=insert.font if unusable else "",
    )


def replace_fit(
    engine: Engine, span: Span, text: str, *, strategy: Strategy = "as-is"
) -> FitReport:
    """What would happen if the user typed this, with the ways out if it will not fit.

    `strategy` is kept only if it's one of the ways out offered; otherwise as-is.
    """
    missing = engine.missing(span, text)
    original = engine.measure(span, span.text)
    delta = engine.measure(span, text) - original
    options = options_for(delta, original)
    return FitReport(
        delta_pt=round(delta, 2),
        missing=missing,
        options=options,
        strategy=strategy_drawn(strategy, options),
        left_out=engine.left_out(span, text),
        stand_in=engine.stand_in(span, text),
        asked=strategy,
    )
