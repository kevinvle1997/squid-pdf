"""What a fit asks of a document: how a line is drawn at a span, how wide, and in what.

Read-only questions, answered by the planner and the fonts the engine is made of,
so a fit and the draw it predicts give the same answer. Asked as `engine.fit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from squidpdf.core.fonts.document import DocumentFonts
from squidpdf.core.fonts.pool import PooledFont
from squidpdf.core.plan import DrawPlan, DrawPlanner
from squidpdf.core.text.fidelity import Fidelity
from squidpdf.core.types import Face, Span


@dataclass(frozen=True, slots=True, eq=False)
class LineFit:
    """The questions a fit asks of a document's spans. Made by `open_engine`."""

    fonts: DocumentFonts
    plans: DrawPlanner  # the same planner the engine draws with

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        """How `text` is drawn at this span: worked out once, for a fit to ask all it needs of.

        Its `missing` are the letters no copy of the span's font in the file really draws,
        each checked for a shape, since a trimmed (subset) font lists letters it emptied; a
        font not in the file is checked against its look-alike. `left_out`: none we have draws.
        """
        return self.plans.plan_for(span, text)

    def has_own_font(self, span: Span) -> bool:
        """Whether the file's own copy of the font can be used, if only for some letters."""
        return self.fonts.own(span) is not None

    def width_of(self, span: Span, plan: DrawPlan) -> float:
        """How wide `plan`'s line is at the span's size, as `Engine.draw` places it."""
        return self.plans.width_of(span, plan, size=span.size)

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would be at the span's size, as `Engine.draw` places it."""
        return self.width_of(span, self.plan_for(span, text))

    def substitute(self, span: Span, text: str, *, plan: DrawPlan) -> str:
        """The face we ship that draws `text` when the span's own font can't: "Carlito Bold".

        `plan` is `text`'s, from `plan_for`.
        """
        drawn_in = plan.drawn_in
        # The own font draws it: the face that would if the page wouldn't take the font.
        if isinstance(drawn_in, PooledFont):
            return self.plans.substitute_for(span, text).face.name
        if isinstance(drawn_in, Face):
            return drawn_in.name
        assert_never(drawn_in)

    def fidelity(self, span: Span, plan: DrawPlan) -> Fidelity:
        """How an edit `plan` draws at the span turns out, as the span's own text is judged."""
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, Face):
            return "substitute"
        # The own font draws it: approximate where it leaves letters out, or a redraw of the
        # span's own text wouldn't look like the page.
        if isinstance(drawn_in, PooledFont):
            unlike = self.plans.unlike(span, self.plans.plan_for(span, span.text))
            set_as_around = not plan.left_out and unlike is None
            return "exact" if set_as_around else "approximate"
        assert_never(drawn_in)
