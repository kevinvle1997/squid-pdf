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
    face: Face
    same_widths: bool
    width_delta_pct: float | None


@dataclass(frozen=True, slots=True, eq=False)
class LineFit:
    fonts: DocumentFonts
    plans: DrawPlanner

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        """A trimmed (subset) font lists letters it emptied, so each is checked for a shape."""
        return self.plans.plan_for(span, text)

    def has_own_font(self, span: Span) -> bool:
        return self.fonts.own(span) is not None

    def width_of(self, span: Span, plan: DrawPlan) -> float:
        return self.plans.width_of(span, plan, size=span.size)

    def measure(self, span: Span, text: str) -> float:
        return self.width_of(span, self.plan_for(span, text))

    def substitute(self, span: Span, text: str, *, plan: DrawPlan) -> str:
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, PooledFont):
            return self.plans.substitute_for(span, text).face.name
        if isinstance(drawn_in, Face):
            return drawn_in.name
        assert_never(drawn_in)

    def substitute_widths(self, span: Span, text: str, *, plan: DrawPlan) -> SubstituteWidths:
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, PooledFont):
            substitute = self.plans.substitute_for(span, text)
            return self._against_own(span, substitute.face, drawn=substitute.text)
        if isinstance(drawn_in, Face):
            return self._against_own(span, drawn_in, drawn=plan.text)
        assert_never(drawn_in)

    def _against_own(self, span: Span, face: Face, *, drawn: str) -> SubstituteWidths:
        if self.fonts.same_widths(span, face):
            return SubstituteWidths(face, same_widths=True, width_delta_pct=0.0)
        # Letters only: a space is set by the page's gaps as much as by the font.
        letters = [ch for ch in drawn if not ch.isspace()]
        own = self._own_widths(span)
        unknown = SubstituteWidths(face, same_widths=False, width_delta_pct=None)
        if any(ch not in own for ch in letters):
            return unknown
        own_width = sum(own[ch] for ch in letters)
        if own_width <= 0:
            return unknown
        face_widths = letter_widths(self.plans.driver.face_font(face), letters)
        face_width = sum(face_widths[ch] for ch in letters)
        delta = round((face_width - own_width) / own_width * _PERCENT, 2)
        return SubstituteWidths(face, same_widths=False, width_delta_pct=delta)

    def _own_widths(self, span: Span) -> dict[str, float]:
        pooled = self.fonts.own(span)
        letters = {} if pooled is None else pooled.every_letter()
        copies = {ch: copy.widths[ch] for ch, copy in letters.items()}
        try:
            listed = self.fonts.listed_widths(span)
        except DriverError:
            listed = None
        return ({} if listed is None else listed) | copies
