"""When a replacement won't fit, the app offers the choice rather than making it.

A silent squeeze looks wrong, and the user never learns why.
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
    name: Strategy
    label: Message
    detail: Message


_SOME_WIDTH_PT = 1.0


@dataclass(frozen=True, slots=True)
class Verdict:
    fidelity: Fidelity
    why: Message | None
    substitute: SubstituteWidths


@dataclass(frozen=True, slots=True)
class FitReport:
    """Facts and option ids: `describe()` makes Messages, so the engine knows no language."""

    delta_pt: float
    room_pt: float = 0.0
    original_pt: float = 0.0
    missing: list[str] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    strategy: Strategy = "as-is"
    left_out: list[str] = field(default_factory=list)
    substitute: str = ""
    asked: Strategy = "as-is"
    unavailable: str = ""

    @property
    def ok(self) -> bool:
        return not self.missing and not self.too_long

    @property
    def too_long(self) -> bool:
        return runs_past(self.delta_pt, self.room_pt)

    @property
    def percent_past_room(self) -> int:
        """A share, not points, which anyone reads; `percentPastRoom` in fit.ts mirrors it."""
        past_pt = self.delta_pt - self.room_pt
        # Rounded first, so a share that's whole in decimal isn't pushed up by binary.
        share = round(past_pt / max(self.original_pt, _SOME_WIDTH_PT) * 100, 6)
        return max(1, math.ceil(share))

    def describe(self) -> list[Message]:
        parts: list[Message] = []
        if self.unavailable:
            params: dict[str, Param] = {"chosen": self.unavailable, "font": self.substitute}
            parts.append(Message("chosen_unavailable", params))
        # Letters its own font lacks but the substitute has: the whole line switches.
        switched = [ch for ch in self.missing if ch not in self.left_out]
        if switched:
            parts.append(Message("missing", {"chars": switched, "font": self.substitute}))
        if self.left_out:
            parts.append(Message("will_leave_out", {"letters": list(self.left_out)}))
        # The part past its room collides, unless a way out ends it where the original did.
        if self.too_long and self.strategy == "as-is":
            parts.append(Message("too_long", {"percent": self.percent_past_room}))
        passed_over = self.asked != "as-is" and self.strategy != self.asked
        if passed_over and self.too_long:
            parts.append(Message("not_offered"))
        return parts


@dataclass(frozen=True, slots=True, eq=False)
class Fitted:
    """`plan` holds the fonts that draw the line, so it stays in the process that made it."""

    report: FitReport
    plan: DrawPlan = field(repr=False)
    verdict: Verdict
    size: float | None = None
    scale_x: float = 1.0


@dataclass(frozen=True, slots=True, eq=False)
class RunFitted:
    span: Span
    text: str
    x: float
    changed: bool
    moved: bool
    report: FitReport
    plan: DrawPlan = field(repr=False)
    put_back: DrawPlan = field(repr=False)
    verdict: Verdict
    size: float | None = None
    scale_x: float = 1.0


@dataclass(frozen=True, slots=True, eq=False)
class LineFitted:
    runs: list[RunFitted]

    @property
    def redrawn(self) -> list[RunFitted]:
        return [run for run in self.runs if run.changed or run.moved]


@dataclass(frozen=True, slots=True, eq=False)
class EditFits:
    replaces: dict[str, Fitted]
    lines: dict[str, LineFitted]
    inserts: dict[int, Fitted]


@dataclass(frozen=True, slots=True)
class RunWidths:
    x: float
    original_pt: float
    typed_pt: float
    changed: bool


@dataclass(frozen=True, slots=True)
class RunsLaidOut:
    """A way out ends the changed runs where they ended, so nothing after them moves."""

    report: FitReport
    scale: float
    starts: list[float]


def runs_laid_out(
    runs: Sequence[RunWidths], *, room_pt: float, strategy: Strategy
) -> RunsLaidOut:
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
    scale = 1.0 if drawn_strategy == "as-is" else original_width / (original_width + delta_pt)
    return RunsLaidOut(report, scale, _run_starts(runs, scale=scale))


def _changed_widths(runs: Sequence[RunWidths]) -> tuple[float, float]:
    changed = [run for run in runs if run.changed]
    original = sum(run.original_pt for run in changed)
    return original, sum(run.typed_pt for run in changed) - original


def _run_starts(runs: Sequence[RunWidths], *, scale: float) -> list[float]:
    """Each keeps the gap the page left after the run before it."""
    gaps = [after.x - (run.x + run.original_pt) for run, after in pairwise(runs)]
    starts = [runs[0].x]
    for run, gap in zip(runs[:-1], gaps, strict=True):
        drawn_pt = run.typed_pt * scale if run.changed else run.typed_pt
        starts.append(starts[-1] + drawn_pt + gap)
    return starts


def runs_past(delta_pt: float, room_pt: float) -> bool:
    return delta_pt > max(TOLERANCE_PT, room_pt + ROOM_SLACK_PT)


def options_for(delta_pt: float, original_width: float, *, room_pt: float) -> list[Option]:
    """Condensing only while invisible, shrinking only to the floor: past that, it looks off."""
    # No width to compare against: the ratios below divide by it.
    if not runs_past(delta_pt, room_pt) or original_width <= 0:
        return []

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
    offered = asked in {option.name for option in options}
    return asked if offered else "as-is"


def insert_span(engine: Engine, insert: Insert) -> Span:
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
    if insert.font not in FACES:
        return verdict
    drawn_as_asked = isinstance(plan.drawn_in, Face) and plan.drawn_in.name == insert.font
    fidelity: Fidelity = "exact" if drawn_as_asked else "substitute"
    return replace(verdict, fidelity=fidelity, why=None)


def replace_fit(
    engine: Engine, span: Span, text: str, *, room_pt: float, strategy: Strategy = "as-is"
) -> Fitted:
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
    if drawn_strategy == "as-is":
        return Fitted(report, typed_plan, verdict)
    if drawn_strategy == "shrink":
        return Fitted(
            report, typed_plan, verdict, size=span.size * original_width / typed_width
        )
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
    if not changed or strategy == "as-is":
        return fitted
    if strategy == "shrink":
        return replace(fitted, size=span.size * laid_out.scale)
    if strategy == "condense":
        return replace(fitted, scale_x=laid_out.scale)
    assert_never(strategy)


def _verdict_of(engine: Engine, span: Span, text: str, *, plan: DrawPlan) -> Verdict:
    report = engine.fidelity_of(span, plan)
    why = report.why if report.state == "approximate" else None
    substitute = engine.fit.substitute_widths(span, text, plan=plan)
    return Verdict(report.state, why=why, substitute=substitute)
