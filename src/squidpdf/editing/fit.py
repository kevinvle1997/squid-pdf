"""Can this text be drawn here, and if not, what are the ways out.

The product rule: when a replacement will not fit, the app offers the choice
rather than making it. A silent 20% squeeze looks wrong and the user never
learns why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from squidpdf.core import (
    CONDENSE_LIMIT,
    FACES,
    OPTION_KEYS,
    SHRINK_FLOOR,
    TOLERANCE_PT,
    Engine,
    Message,
    Param,
    Span,
    index_of,
    new_text,
)
from squidpdf.editing.edits import Insert, Strategy


@dataclass(frozen=True, slots=True)
class Option:
    """One way to make a too-long replacement work: its name, and what the user reads of it."""

    name: Strategy
    label: Message
    detail: Message


@dataclass(frozen=True, slots=True)
class FitReport:
    """The answer to 'what happens if I type this'.

    Carries facts and option *identifiers*. What's wrong comes out of
    `describe()` as Messages, which the edge puts into the reader's words, so
    the engine never learns about language. `strategy` is the one drawn: the
    one asked for if it was offered, else as-is.

    It describes what will really be drawn: `delta_pt` is measured without the
    `left_out` letters, since they won't be there.
    """

    delta_pt: float
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
        return not self.missing and self.delta_pt <= TOLERANCE_PT

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
        if self.delta_pt > TOLERANCE_PT:
            parts.append(Message("too_long", {"delta_pt": self.delta_pt}))
        # The user's choice of way out wasn't one on offer.
        passed_over = self.asked != "as-is" and self.strategy != self.asked
        if passed_over and self.delta_pt > TOLERANCE_PT:
            parts.append(Message("not_offered"))
        return parts


@dataclass(frozen=True, slots=True)
class LogFits:
    """A fit for every replace and insert an edit log leaves, drawn or not."""

    replaces: dict[str, FitReport]  # by the replaced span's id
    inserts: dict[int, FitReport]  # by the insert's place in the log


def options_for(delta_pt: float, original_width: float) -> list[Option]:
    """The three ways out of an overflow, in the order worth trying.

    Condensing is offered only while it stays invisible, and shrinking only down
    to the floor. Past that it is not a solution, it is a different-looking page.
    """
    # It fits, or there's no width to compare against.
    if delta_pt <= TOLERANCE_PT or original_width <= 0:
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


def strategy_drawn(asked: Strategy, options: list[Option]) -> Strategy:
    """The way out that's drawn: the one asked for if it was offered, else as-is."""
    offered = asked in {option.name for option in options}
    return asked if offered else "as-is"


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
        strategy=strategy_drawn(strategy, options),
        left_out=typed_plan.left_out,
        substitute=engine.substitute(span, text, plan=typed_plan),
        asked=strategy,
    )
