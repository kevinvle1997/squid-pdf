"""What a fit asks of a document: how a line is drawn at a span, how wide, and in what.

Read-only questions, answered by the planner and the fonts the engine is made of,
so a fit and the draw it predicts give the same answer. Asked as `engine.fit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from squidpdf.core.fonts.document import DocumentFonts
from squidpdf.core.fonts.pool import PooledFont
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.plan import DrawPlan, DrawPlanner, letter_widths
from squidpdf.core.types import Face, Span

_PERCENT = 100


@dataclass(frozen=True, slots=True)
class SubstituteWidths:
    """A face that draws in a span's font's place, and how its letters' widths compare."""

    face: Face
    same_widths: bool  # its letters are as wide as the font's, by its look-alike's match
    # How much wider it sets the line's letters than the font would; None where the font
    # gives no width for one of them.
    width_delta_pct: float | None


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

    def substitute_widths(self, span: Span, text: str, *, plan: DrawPlan) -> SubstituteWidths:
        """The face that draws `text` in the span's font's place, or would if the page refused.

        `plan` is `text`'s, from `plan_for`.
        """
        drawn_in = plan.drawn_in
        # The own font draws it: the face that would if the page wouldn't take the font.
        if isinstance(drawn_in, PooledFont):
            substitute = self.plans.substitute_for(span, text)
            return self._against_own(span, substitute.face, drawn=substitute.text)
        if isinstance(drawn_in, Face):
            return self._against_own(span, drawn_in, drawn=plan.text)
        assert_never(drawn_in)

    def _against_own(self, span: Span, face: Face, *, drawn: str) -> SubstituteWidths:
        """`face` drawing `drawn` at the span, its letters' widths against the span's font's."""
        if self.fonts.same_widths(span, face):
            return SubstituteWidths(face, same_widths=True, width_delta_pct=0.0)
        # Letters only: a space is set by the page's gaps as much as by the font.
        letters = [ch for ch in drawn if not ch.isspace()]
        own = self._own_widths(span)
        unknown = SubstituteWidths(face, same_widths=False, width_delta_pct=None)
        # A letter the font gives no width, so how it would set the line isn't known.
        if any(ch not in own for ch in letters):
            return unknown
        own_width = sum(own[ch] for ch in letters)
        # No letter with a width, as a line of spaces: nothing to take a share of.
        if own_width <= 0:
            return unknown
        face_widths = letter_widths(self.plans.driver.face_font(face), letters)
        face_width = sum(face_widths[ch] for ch in letters)
        delta = round((face_width - own_width) / own_width * _PERCENT, 2)
        return SubstituteWidths(face, same_widths=False, width_delta_pct=delta)

    def _own_widths(self, span: Span) -> dict[str, float]:
        """Each letter's width in the span's font, per 1000 em: its copies', else its list's."""
        pooled = self.fonts.own(span)
        letters = {} if pooled is None else pooled.every_letter()
        copies = {ch: copy.widths[ch] for ch, copy in letters.items()}
        try:
            listed = self.fonts.listed_widths(span)
        except DriverError:  # the library can't load the font, so its list can't be read
            listed = None
        return ({} if listed is None else listed) | copies
