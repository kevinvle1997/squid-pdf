from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import assert_never

import squidpdf.core.constants as constants
from squidpdf.core.app.message import Message
from squidpdf.core.fonts.document import DocumentFonts
from squidpdf.core.fonts.pool import CodedRun, PooledFont
from squidpdf.core.fonts.substitute import Substitute, face_coverage, face_letters
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from squidpdf.core.text.fidelity import why_approximate
from squidpdf.core.text.spacing import Word, lacks_space, placed_words, span_gaps
from squidpdf.core.types import EM, Face, FontCode, Span

_WIDTH_DP = 2  # finer than any page can show
# Hebrew and Arabic letters (R, AL) and Arabic digits (AN), which read back reversed.
_RIGHT_TO_LEFT = frozenset({"R", "AL", "AN"})


def letter_widths(font: FontProgram, letters: Iterable[str]) -> dict[str, float]:
    return {ch: round(font.advance(ch) * EM, _WIDTH_DP) for ch in letters}


@dataclass(frozen=True, slots=True, eq=False)
class DrawPlan:
    drawn_in: PooledFont | Face
    text: str
    missing: list[str]
    left_out: list[str]
    codes: Mapping[str, FontCode] = field(default_factory=dict)
    letter_spacing: float = 0.0

    def letter_spacing_at(self, size: float) -> float:
        return self.letter_spacing * size


@dataclass(frozen=True, slots=True, eq=False)
class DrawPlanner:
    fonts: DocumentFonts
    driver: PdfDriver

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        own = self.fonts.own(span)
        spacing = self.fonts.letter_spacing(span, own)
        return replace(self._plan_unspaced(span, text, own), letter_spacing=spacing)

    def _plan_unspaced(self, span: Span, text: str, own: PooledFont | None) -> DrawPlan:
        if own is None:
            return self._plan_in_a_face(span, unicodedata.normalize("NFC", text))
        text = _spelled(own, text)
        missing = own.missing(text)
        codes = self.fonts.drawn_codes(span, own)
        if not missing:
            return DrawPlan(own, text=text, missing=[], left_out=[], codes=codes)
        substitute = self.fonts.substitute(span, text)
        # Nothing we have draws what it lacks: switching would gain nothing.
        undrawable = set(missing) <= set(substitute.left_out)
        if undrawable:
            kept = "".join(ch for ch in text if ch not in missing)
            return DrawPlan(own, text=kept, missing=missing, left_out=missing, codes=codes)
        return DrawPlan(
            substitute.face, text=substitute.text, missing=missing, left_out=substitute.left_out
        )

    def _plan_in_a_face(self, span: Span, composed: str) -> DrawPlan:
        look_alike = self.fonts.look_alike(span).face
        missing = face_coverage(look_alike).missing(composed)
        if not missing:
            return DrawPlan(look_alike, text=composed, missing=[], left_out=[])
        substitute = self.fonts.substitute(span, composed)
        return DrawPlan(
            substitute.face, text=substitute.text, missing=missing, left_out=substitute.left_out
        )

    def substitute_for(self, span: Span, text: str) -> Substitute:
        return self.fonts.substitute(span, unicodedata.normalize("NFC", text))

    def unlike(self, span: Span, plan: DrawPlan, *, own_text: DrawPlan) -> Message | None:
        # Turned on the page: redraws are level.
        if span.turned:
            return why_approximate("turned_text")
        # Arabic or Hebrew: a span is merged and redrawn as if it ran left to right.
        if _in_right_to_left_script(span.text):
            return why_approximate("right_to_left_text")
        if plan.left_out:
            return why_approximate("undrawable_letters", {"letters": list(plan.left_out)})
        if not span.fragments:
            return None
        if not isinstance(own_text.drawn_in, PooledFont):
            return None
        # Spaced or stretched (letter spacing, scaling, a justified line): redraws close it up.
        redrawn = self.width_of(span, own_text, size=span.size)
        if abs(span.bbox.width - redrawn) > constants.TOLERANCE_PT:
            return why_approximate("spaced_text")
        return None

    def width_of(self, span: Span, plan: DrawPlan, *, size: float) -> float:
        by_code = coded_in(plan)
        spacing = plan.letter_spacing_at(size)
        if by_code is not None:
            widths = (run.coded.letters[ch].width for run in by_code for ch in run.text)
            return sum(widths) * size / EM + spacing * len(plan.text)
        font = self._program_of(plan)
        _words, width = self.words_of(span, plan.text, font=font, size=size, spacing=spacing)
        return width

    def _program_of(self, plan: DrawPlan) -> FontProgram:
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, PooledFont):
            return drawn_in
        if isinstance(drawn_in, Face):
            return self.driver.face_font(drawn_in)
        assert_never(drawn_in)

    def words_of(
        self, span: Span, text: str, *, font: FontProgram, size: float, spacing: float
    ) -> tuple[list[Word], float]:
        one_run = not lacks_space(font) or " " not in text
        if one_run:
            return [Word(text, 0.0)], font.width(text, size) + spacing * len(text)
        gaps, usual = span_gaps(span, font), self.fonts.usual_gap(span, font)
        return placed_words(text, font=font, size=size, gaps=gaps, usual=usual, spacing=spacing)

    def widths(self, span: Span) -> dict[str, float]:
        """With no space in the font, a space is the page's usual gap."""
        pooled = self.fonts.own(span)

        if pooled is None:
            face = self.fonts.look_alike(span).face
            return letter_widths(self.driver.face_font(face), face_letters(face))

        letters = sorted(pooled.every_letter().items())
        widths = {ch: round(copy.widths[ch], _WIDTH_DP) for ch, copy in letters}
        spaceless = not pooled.is_coded() and lacks_space(pooled)
        if spaceless:
            widths[" "] = round(self.fonts.usual_gap(span, pooled) * EM, _WIDTH_DP)
        return widths


def coded_in(plan: DrawPlan) -> list[CodedRun] | None:
    drawn_in = plan.drawn_in
    if isinstance(drawn_in, Face):
        return None
    if isinstance(drawn_in, PooledFont) and not drawn_in.is_coded():
        return None
    if isinstance(drawn_in, PooledFont):
        return drawn_in.coded_runs(plan.text, drawn=plan.codes)
    assert_never(drawn_in)


def _spelled(own: PooledFont, text: str) -> str:
    """A trimmed font keeps whichever form of é its document used; macOS pastes in pieces."""
    composed = unicodedata.normalize("NFC", text)
    in_pieces = unicodedata.normalize("NFD", text)
    whole = (spelling for spelling in (text, composed, in_pieces) if not own.missing(spelling))
    return next(whole, composed)


def _in_right_to_left_script(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in _RIGHT_TO_LEFT for ch in text)
