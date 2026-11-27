"""Can this text be drawn here, and if not, what are the ways out.

The product rule: when a replacement will not fit, the app offers the choice
rather than making it. A silent 20% squeeze looks wrong and the user never
learns why.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import assert_never

from squidpdf.core import (
    CONDENSE_LIMIT,
    FACES,
    MOVED_PT,
    OPTION_KEYS,
    ROOM_SLACK_PT,
    SHRINK_FLOOR,
    TOLERANCE_PT,
    DrawPlan,
    Engine,
    Face,
    Fidelity,
    Message,
    Param,
    Span,
    SubstituteWidths,
    new_text,
)
from squidpdf.editing.edits import Insert, Strategy


@dataclass(frozen=True, slots=True)
class Option:
    """One way to make a too-long replacement work: its name, and what the user reads of it."""

    name: Strategy
    label: Message
    detail: Message


# A line's width when it has none to speak of: what a share past its room is taken of.
_SOME_WIDTH_PT = 1.0


@dataclass(frozen=True, slots=True)
class Verdict:
    """How an edit comes out: in its span's own font, set as the page sets the span, or not.

    The same verdict a span gets at upload (`Engine.fidelity_of`), for the edit's own words.
    """

    fidelity: Fidelity
    why: Message | None  # how an approximate edit won't match the text around it
    # The face that draws it in its font's place, or would were the font refused.
    substitute: SubstituteWidths


@dataclass(frozen=True, slots=True)
class FitReport:
    """The answer to 'what happens if I type this'.

    Facts and option identifiers: what's wrong comes out of `describe()` as Messages, so the
    engine never learns about language. `strategy` is the one drawn: the one asked for if
    offered, else as-is. `delta_pt` leaves out `left_out`, since those won't be drawn.
    """

    delta_pt: float
    room_pt: float = 0.0  # the free space after it on its line (`core.text.room`)
    original_pt: float = (
        0.0  # how wide the original line is: what a line too long is a share of
    )
    missing: list[str] = field(default_factory=list)  # the span's own font lacks these
    options: list[Option] = field(default_factory=list)
    strategy: Strategy = "as-is"
    left_out: list[str] = field(default_factory=list)  # no font we have draws these
    substitute: str = ""  # the face that draws the line when its own font can't
    asked: Strategy = "as-is"  # the way out the user chose, offered or not
    unavailable: str = ""  # new text's chosen font, when it can't be used here at all

    @property
    def ok(self) -> bool:
        """True when the replacement can be drawn as-is, no compromise needed."""
        return not self.missing and not self.too_long

    @property
    def too_long(self) -> bool:
        """Whether the line runs past its room by more than the tolerance would allow."""
        return runs_past(self.delta_pt, self.room_pt)

    @property
    def percent_past_room(self) -> int:
        """How far past its room the line runs, as a whole percent of the original, at least 1.

        A share, not points, which anyone reads: `percentPastRoom` in react/src/editing/fit.ts.
        """
        past_pt = self.delta_pt - self.room_pt
        # Rounded first, so a share that's whole in decimal isn't pushed up by binary.
        share = round(past_pt / max(self.original_pt, _SOME_WIDTH_PT) * 100, 6)
        return max(1, math.ceil(share))

    def describe(self) -> list[Message]:
        """Everything that won't come out as typed, in the order it's told; empty if nothing."""
        parts: list[Message] = []
        # New text in a font that can't be used here: all of it is in the substitute.
        if self.unavailable:
            params: dict[str, Param] = {"chosen": self.unavailable, "font": self.substitute}
            parts.append(Message("chosen_unavailable", params))
        # Letters its own font lacks but the substitute has: the whole line switches.
        switched = [ch for ch in self.missing if ch not in self.left_out]
        if switched:
            parts.append(Message("missing", {"chars": switched, "font": self.substitute}))
        # Letters nothing can draw.
        if self.left_out:
            parts.append(Message("will_leave_out", {"letters": list(self.left_out)}))
        # The part past its room collides, unless a way out ends it where the original did.
        if self.too_long and self.strategy == "as-is":
            parts.append(Message("too_long", {"percent": self.percent_past_room}))
        # The user's choice of way out wasn't one on offer.
        passed_over = self.asked != "as-is" and self.strategy != self.asked
        if passed_over and self.too_long:
            parts.append(Message("not_offered"))
        return parts


@dataclass(frozen=True, slots=True, eq=False)
class Fitted:
    """A fit and the plan it measured, which `draw` then draws: one plan for both.

    `plan` holds the fonts that draw the line, so it stays in the process that made it.
    `verdict` is the plan's (`verdicts_of` says it as drawn). A None `size` keeps the
    span's own; `scale_x` narrows the line from its start.
    """

    report: FitReport
    plan: DrawPlan = field(repr=False)
    verdict: Verdict
    size: float | None = None
    scale_x: float = 1.0


@dataclass(frozen=True, slots=True, eq=False)
class RunFitted:
    """One run of a line's edit as it's drawn: its words, where it starts, in what plan.

    `report` is the line's fit with this run's own letters and face. `x` is where it starts
    once the runs before it are drawn; `put_back` plans its own words, to draw it as it was.
    """

    span: Span  # as the document has it
    text: str
    x: float
    changed: bool  # its words are new
    moved: bool  # it starts elsewhere than it did
    report: FitReport
    plan: DrawPlan = field(repr=False)
    put_back: DrawPlan = field(repr=False)
    verdict: Verdict
    size: float | None = None
    scale_x: float = 1.0


@dataclass(frozen=True, slots=True, eq=False)
class LineFitted:
    """A line's edit, run by run: one neither changed nor moved is left as the page has it."""

    runs: list[RunFitted]

    @property
    def redrawn(self) -> list[RunFitted]:
        """The runs it erases and draws again: changed, or moved by a change before them."""
        return [run for run in self.runs if run.changed or run.moved]


@dataclass(frozen=True, slots=True, eq=False)
class EditFits:
    """A fit for every replace, line edit and insert an edit list leaves, drawn or not."""

    replaces: dict[str, Fitted]  # by the replaced span's id
    lines: dict[str, LineFitted]  # by the id of the line's first span
    inserts: dict[int, Fitted]  # by the insert's place in the edit list


@dataclass(frozen=True, slots=True)
class RunWidths:
    """One run of a line, measured: where the page starts it, and its words' widths."""

    x: float
    original_pt: float  # its own words'
    typed_pt: float  # the words it's to read
    changed: bool


@dataclass(frozen=True, slots=True)
class RunsLaidOut:
    """A line's runs fitted by those that change: the line's fit, and where each run starts.

    `scale` is how wide the changed runs are drawn, as a share of their typed width: a way
    out ends them where they ended, so nothing after them moves.
    """

    report: FitReport  # the line's, without any one run's letters or face
    scale: float
    starts: list[float]


def runs_laid_out(
    runs: Sequence[RunWidths], *, room_pt: float, strategy: Strategy
) -> RunsLaidOut:
    """A line's runs as typed, fitted in its room by those that change, and set along it."""
    original_width, delta_pt = _changed_widths(runs)
    options = options_for(delta_pt, original_width, room_pt=room_pt)
    drawn_strategy = _strategy_drawn(strategy, options)
    report = FitReport(
        delta_pt=round(delta_pt, 2),
        room_pt=round(room_pt, 2),
        original_pt=round(original_width, 2),
        options=options,
        strategy=drawn_strategy,
        asked=strategy,
    )
    # A way out is offered only past the room, so the changed runs have some width then.
    scale = 1.0 if drawn_strategy == "as-is" else original_width / (original_width + delta_pt)
    return RunsLaidOut(report, scale, _run_starts(runs, scale=scale))


def _changed_widths(runs: Sequence[RunWidths]) -> tuple[float, float]:
    """How wide a line's changed runs were, and how much wider they're typed."""
    changed = [run for run in runs if run.changed]
    original = sum(run.original_pt for run in changed)
    return original, sum(run.typed_pt for run in changed) - original


def _run_starts(runs: Sequence[RunWidths], *, scale: float) -> list[float]:
    """Where each run starts once the runs before it are drawn, the changed ones `scale` wide.

    Each keeps the gap the page left after the run before it, so a run with no change
    before it starts where it did.
    """
    gaps = [after.x - (run.x + run.original_pt) for run, after in pairwise(runs)]
    starts = [runs[0].x]
    for run, gap in zip(runs[:-1], gaps, strict=True):
        drawn_pt = run.typed_pt * scale if run.changed else run.typed_pt
        starts.append(starts[-1] + drawn_pt + gap)
    return starts


def runs_past(delta_pt: float, room_pt: float) -> bool:
    """Whether a line `delta_pt` longer runs past its room, or the tolerance if wider."""
    return delta_pt > max(TOLERANCE_PT, room_pt + ROOM_SLACK_PT)


def options_for(delta_pt: float, original_width: float, *, room_pt: float) -> list[Option]:
    """The three ways out of an overflow, in the order worth trying.

    Condensing is offered only while it stays invisible, and shrinking only down
    to the floor. Past that it is not a solution, it is a different-looking page.
    Each ends the line where the original did.
    """
    # No width to compare against: the ratios below divide by it.
    if not runs_past(delta_pt, room_pt) or original_width <= 0:
        return []

    # What shrinking to fit would scale the size to, and how much condensing squeezes.
    shrunk_to = original_width / (original_width + delta_pt)
    squeezed_by = delta_pt / original_width
    names: list[Strategy] = []
    if shrunk_to >= SHRINK_FLOOR:
        names.append("shrink")
    if squeezed_by <= CONDENSE_LIMIT:
        names.append("condense")
    names.append("as-is")
    return [
        Option(
            name,
            Message(OPTION_KEYS[name].label),
            Message(OPTION_KEYS[name].detail, {"delta_pt": delta_pt}),
        )
        for name in names
    ]


def _strategy_drawn(asked: Strategy, options: list[Option]) -> Strategy:
    """The way out that's drawn: the one asked for if it was offered, else as-is."""
    offered = asked in {option.name for option in options}
    return asked if offered else "as-is"


def insert_span(engine: Engine, insert: Insert) -> Span:
    """An insert as a span, so it's judged, measured and drawn exactly like an edit."""
    return new_text(
        insert.page,
        origin=insert.origin,
        text=insert.text,
        size=insert.size,
        font=insert.font,
        font_object=engine.font_object_named(
            insert.page, insert.font, font_object=insert.font_object
        ),
        color=insert.color,
    )


def insert_fit(engine: Engine, insert: Insert) -> Fitted:
    """What new text will look like: its font and letters, as there's no width to fit."""
    span = insert_span(engine, insert)
    # A font that only lacks a letter is still usable: a substitute draws the line.
    unusable = insert.font not in FACES and not engine.fit.has_own_font(span)
    typed_plan = engine.fit.plan_for(span, insert.text)
    verdict = _verdict_of(engine, span, insert.text, plan=typed_plan)
    report = FitReport(
        delta_pt=0.0,
        missing=[] if unusable else typed_plan.missing,
        left_out=typed_plan.left_out,
        substitute=engine.fit.substitute(span, insert.text, plan=typed_plan),
        unavailable=insert.font if unusable else "",
    )
    return Fitted(report, typed_plan, _new_text_verdict(insert, typed_plan, verdict))


def _new_text_verdict(insert: Insert, plan: DrawPlan, verdict: Verdict) -> Verdict:
    """New text's verdict: in a face we ship, exact where it draws, else a similar face's."""
    # In a font of the page's: judged as an edit of that font is.
    if insert.font not in FACES:
        return verdict
    drawn_as_asked = isinstance(plan.drawn_in, Face) and plan.drawn_in.name == insert.font
    fidelity: Fidelity = "exact" if drawn_as_asked else "substitute"
    return replace(verdict, fidelity=fidelity, why=None)


def replace_fit(
    engine: Engine, span: Span, text: str, *, room_pt: float, strategy: Strategy = "as-is"
) -> Fitted:
    """What would happen if the user typed this, with the ways out if it will not fit.

    `room_pt` is the span's room (`Engine.rooms`). `strategy` is drawn only if it
    was offered; otherwise as-is.
    """
    # One plan for what's typed: the fit reads it, and the draw draws it.
    typed_plan = engine.fit.plan_for(span, text)
    original_width = engine.fit.measure(span, span.text)
    typed_width = engine.fit.width_of(span, typed_plan)
    delta_pt = typed_width - original_width
    options = options_for(delta_pt, original_width, room_pt=room_pt)
    drawn_strategy = _strategy_drawn(strategy, options)
    report = FitReport(
        delta_pt=round(delta_pt, 2),
        room_pt=round(room_pt, 2),
        original_pt=round(original_width, 2),
        missing=typed_plan.missing,
        options=options,
        strategy=drawn_strategy,
        left_out=typed_plan.left_out,
        substitute=engine.fit.substitute(span, text, plan=typed_plan),
        asked=strategy,
    )
    verdict = _verdict_of(engine, span, text, plan=typed_plan)
    # The span's size, no stretch.
    if drawn_strategy == "as-is":
        return Fitted(report, typed_plan, verdict)
    # Width goes with size, so it ends where the original did.
    if drawn_strategy == "shrink":
        return Fitted(
            report, typed_plan, verdict, size=span.size * original_width / typed_width
        )
    # The same size, letters squeezed narrower, to the same end.
    if drawn_strategy == "condense":
        return Fitted(report, typed_plan, verdict, scale_x=original_width / typed_width)
    assert_never(drawn_strategy)


def line_fit(
    engine: Engine,
    spans: Sequence[Span],
    texts: Sequence[str],
    *,
    room_pt: float,
    strategy: Strategy = "as-is",
) -> LineFitted:
    """What a line's runs come to typed as `texts`, fitted by the runs that change.

    `room_pt` is the line's own (`Line`). As is, the runs after a change move along with it;
    a way out scales only the changed runs, to their width, so nothing after them moves.
    """
    own = [engine.fit.plan_for(span, span.text) for span in spans]
    plans = [
        own_plan if text == span.text else engine.fit.plan_for(span, text)
        for span, text, own_plan in zip(spans, texts, own, strict=True)
    ]
    widths = [
        RunWidths(
            span.origin[0],
            original_pt=engine.fit.width_of(span, own_plan),
            typed_pt=engine.fit.width_of(span, plan),
            changed=text != span.text,
        )
        for span, text, plan, own_plan in zip(spans, texts, plans, own, strict=True)
    ]
    laid_out = runs_laid_out(widths, room_pt=room_pt, strategy=strategy)
    return LineFitted(
        [
            _run_fitted(
                engine,
                span,
                text,
                x=x,
                changed=run.changed,
                plans=(plan, own_plan),
                laid_out=laid_out,
            )
            for span, text, x, run, plan, own_plan in zip(
                spans, texts, laid_out.starts, widths, plans, own, strict=True
            )
        ]
    )


def _run_fitted(
    engine: Engine,
    span: Span,
    text: str,
    *,
    x: float,
    changed: bool,
    plans: tuple[DrawPlan, DrawPlan],
    laid_out: RunsLaidOut,
) -> RunFitted:
    """One run of a line's edit, as `laid_out` sets it: `plans` are its typed and own words'."""
    plan, own_plan = plans
    report = replace(
        laid_out.report,
        missing=plan.missing,
        left_out=plan.left_out,
        substitute=engine.fit.substitute(span, text, plan=plan),
    )
    fitted = RunFitted(
        span,
        text,
        x=x,
        changed=changed,
        moved=abs(x - span.origin[0]) > MOVED_PT,
        report=report,
        plan=plan,
        put_back=own_plan,
        verdict=_verdict_of(engine, span, text, plan=plan),
    )
    strategy = laid_out.report.strategy
    # Unchanged, or no way out: at the span's size, no squeeze.
    if not changed or strategy == "as-is":
        return fitted
    if strategy == "shrink":
        return replace(fitted, size=span.size * laid_out.scale)
    if strategy == "condense":
        return replace(fitted, scale_x=laid_out.scale)
    assert_never(strategy)


def _verdict_of(engine: Engine, span: Span, text: str, *, plan: DrawPlan) -> Verdict:
    """How `text`, drawn at `span` as `plan` plans it, comes out, judged as upload judges."""
    report = engine.fidelity_of(span, plan)
    why = report.why if report.state == "approximate" else None
    substitute = engine.fit.substitute_widths(span, text, plan=plan)
    return Verdict(report.state, why=why, substitute=substitute)
