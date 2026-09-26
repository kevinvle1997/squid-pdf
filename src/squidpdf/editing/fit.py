"""Can this text be drawn here, and if not, what are the ways out.

The product rule: when a replacement will not fit, the app offers the choice
rather than making it. A silent 20% squeeze looks wrong and the user never
learns why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from squidpdf.core import words
from squidpdf.core.constants import CONDENSE_LIMIT, SHRINK_FLOOR, TOLERANCE_PT
from squidpdf.editing.types import Strategy


@dataclass(frozen=True, slots=True)
class Option:
    """One way to make a too-long replacement work."""

    name: Strategy
    label: str  # shown to the user, verbatim
    detail: str


@dataclass(slots=True)
class FitCheck:
    """The answer to 'what happens if I type this'.

    Carries facts and option *identifiers*. The user-facing sentence is assembled
    in `describe()` rather than baked into the data, so the API layer can
    localise it later without the engine knowing about language. `strategy` is
    the one drawn: the one asked for if it was offered, else as-is.
    """

    delta_pt: float
    missing: list[str] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    strategy: Strategy = "as-is"

    @property
    def ok(self) -> bool:
        """True when the replacement can be drawn as-is, no compromise needed."""
        return not self.missing and self.delta_pt <= TOLERANCE_PT

    def describe(self) -> str | None:
        """Plain language, or None when nothing is wrong. Never jargon."""
        if self.missing:
            return words.MISSING.format(chars=" or ".join(self.missing))
        if self.delta_pt > TOLERANCE_PT:
            return words.TOO_LONG.format(delta_pt=f"{self.delta_pt:.1f}")
        return None


def options_for(delta_pt: float, original_width: float) -> list[Option]:
    """The three ways out of an overflow, in the order worth trying.

    Condensing is offered only while it stays invisible, and shrinking only down
    to the floor. Past that it is not a solution, it is a different-looking page.
    """
    if delta_pt <= TOLERANCE_PT or original_width <= 0:
        return []

    names: list[Strategy] = []
    if original_width / (original_width + delta_pt) >= SHRINK_FLOOR:
        names.append("shrink")
    if delta_pt / original_width <= CONDENSE_LIMIT:
        names.append("condense")
    names.append("as-is")
    return [
        Option(
            name,
            words.OPTIONS[name]["label"],
            words.OPTIONS[name]["detail"].format(delta_pt=f"{delta_pt:.1f}"),
        )
        for name in names
    ]
