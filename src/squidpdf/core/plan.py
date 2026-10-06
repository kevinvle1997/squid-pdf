"""What draws a line: the one plan the fit and the draw share, and the widths it gives.

Read-only: nothing here changes the document. `core.engine` judges and fits
with it, and `core.writer` draws what it says, so the fit's words describe
exactly what's drawn.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import assert_never

from squidpdf.core.app.message import Message
from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.core.fonts.document import DocumentFonts
from squidpdf.core.fonts.pool import CodedRun, PooledFont
from squidpdf.core.fonts.substitute import Substitute, face_coverage, face_letters
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from squidpdf.core.text.fidelity import why_approximate
from squidpdf.core.text.spacing import Word, lacks_space, placed_words, span_gaps
from squidpdf.core.types import EM, Face, Span

_WIDTH_DP = 2  # finer than any page can show
# Unicode bidirectional classes of Hebrew and Arabic letters (R, AL), and Arabic digits (AN),
# which read back reversed.
_RIGHT_TO_LEFT = frozenset({"R", "AL", "AN"})


def letter_widths(font: FontProgram, letters: Iterable[str]) -> dict[str, float]:
    """Each letter's width in `font`, per 1000 em, as the browser gets it."""
    return {ch: round(font.advance(ch) * EM, _WIDTH_DP) for ch in letters}


@dataclass(frozen=True, slots=True)
class DrawPlan:
    """How a line is drawn: the font that draws it, and the line as it comes out.

    Worked out once, and read alike by measuring, judging and drawing, so what
    the fit says is what `draw` does.
    """

    drawn_in: PooledFont | Face  # the file's own copies of the font, pooled, or a face we ship
    text: str  # the line as drawn: spelled as that font has it, less the letters left out
    missing: list[str]  # letters the span's own font lacks: its look-alike's, when it has none
    left_out: list[str]  # letters no font we have can draw, each once, in the order typed


@dataclass(frozen=True, slots=True, eq=False)
class DrawPlanner:
    """Answers what draws a line at a span, and how wide it comes out, for one document."""

    fonts: DocumentFonts
    driver: PdfDriver  # opens the faces we ship to measure with

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        """How `text` is drawn at this span: the one answer the fit and the draw share.

        In the file's copies of the span's font if they draw every character;
        otherwise the whole line in the substitute, less what even that can't
        draw. When the only letters the own font lacks are ones no font we have
        draws, switching would draw none of them, so the own font keeps the
        line without them.
        """
        own = self.fonts.own(span)
        # Not in the file, or unusable: a face we ship draws it, less what even it lacks.
        if own is None:
            composed = unicodedata.normalize("NFC", text)
            look_alike = self.fonts.look_alike(span).face
            missing = face_coverage(look_alike).missing(composed)
            substitute = self.substitute_for(span, composed)
            return DrawPlan(substitute.face, substitute.text, missing, substitute.left_out)
        text = _spelled(own, text)
        missing = own.missing(text)
        # The own font draws every letter.
        if not missing:
            return DrawPlan(own, text, [], [])
        substitute = self.fonts.substitute(span, text)
        # Nothing we have draws what it lacks: switching would gain nothing.
        undrawable = set(missing) <= set(substitute.left_out)
        if undrawable:
            kept = "".join(ch for ch in text if ch not in missing)
            return DrawPlan(own, kept, missing, missing)
        return DrawPlan(substitute.face, substitute.text, missing, substitute.left_out)

    def substitute_for(self, span: Span, text: str) -> Substitute:
        """The face that draws `text` if the page won't take the span's own font after all."""
        return self.fonts.substitute(span, unicodedata.normalize("NFC", text))

    def unlike(self, span: Span, plan: DrawPlan) -> Message | None:
        """How a redraw of the span's own text in its own font looks unlike it; None if not.

        Said as one of the `ApproximateReason`s.
        """
        # Turned on the page: redraws are level.
        if span.turned:
            return why_approximate("turned_text")
        # Arabic or Hebrew: a span is merged and redrawn as if it ran left to right.
        if _in_right_to_left_script(span.text):
            return why_approximate("right_to_left_text")
        # Letters no font we have draws: a redraw leaves them out.
        if plan.left_out:
            return why_approximate("undrawable_letters", {"letters": list(plan.left_out)})
        # New text: its box is only nominal, so there's no spacing of its own to keep.
        if not span.fragments:
            return None
        # Spaced or stretched (letter spacing, scaling, a justified line): redraws close it up.
        redrawn = self.width_of(span, plan, size=span.size)
        if abs(span.bbox.width - redrawn) > TOLERANCE_PT:
            return why_approximate("spaced_text")
        return None

    def width_of(self, span: Span, plan: DrawPlan, *, size: float) -> float:
        """How wide `plan`'s line is, placed as `draw` places it, at `size` points."""
        by_code = coded_in(plan)
        # Written by code: widths come from each copy's width list.
        if by_code is not None:
            widths = (run.coded.letters[ch].width for run in by_code for ch in run.text)
            return sum(widths) * size / EM
        _words, width = self.words_of(span, plan.text, font=self.program_of(plan), size=size)
        return width

    def program_of(self, plan: DrawPlan) -> FontProgram:
        """The font program that measures and draws `plan`'s line."""
        drawn_in = plan.drawn_in
        # The file's own copies, pooled: they measure as one font.
        if isinstance(drawn_in, PooledFont):
            return drawn_in
        # A face we ship, opened to measure with.
        if isinstance(drawn_in, Face):
            return self.driver.face_font(drawn_in)
        assert_never(drawn_in)

    def words_of(
        self, span: Span, text: str, *, font: FontProgram, size: float
    ) -> tuple[list[Word], float]:
        """`text` as `draw` places it in `font`, and where the pen ends: its width."""
        one_run = not lacks_space(font) or " " not in text
        if one_run:
            return [Word(text, 0.0)], font.width(text, size)
        # No space to draw: each word goes where the file's gaps put it.
        gaps, usual = span_gaps(span, font), self.fonts.usual_gap(span, font)
        return placed_words(text, font=font, size=size, gaps=gaps, usual=usual)

    def widths(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em.

        The same font `LineFit.measure` uses, so the browser's sum agrees with it, except
        in a font with no space: a space here is the page's usual gap, where
        `LineFit.measure` keeps the line's own, so a justified line can differ.
        """
        pooled = self.fonts.own(span)

        # Not in the file: the look-alike draws it, its list kept to GLYPH_LIST_RANGES.
        if pooled is None:
            face = self.fonts.look_alike(span).face
            return letter_widths(self.driver.face_font(face), face_letters(face))

        # Each from the copy that draws it: its width list if written by code, else the font.
        letters = sorted(pooled.letters.items())
        widths = {ch: round(copy.widths[ch], _WIDTH_DP) for ch, copy in letters}
        # Written by letter, with no space of its own: a space is the page's usual gap.
        spaceless = not pooled.is_coded() and lacks_space(pooled)
        if spaceless:
            widths[" "] = round(self.fonts.usual_gap(span, pooled) * EM, _WIDTH_DP)
        return widths


def coded_in(plan: DrawPlan) -> list[CodedRun] | None:
    """`plan`'s line in the codes of the file's copies of its font, a run per copy.

    None unless they draw it by code.
    """
    drawn_in = plan.drawn_in
    # A face we ship: no codes.
    if isinstance(drawn_in, Face):
        return None
    # The file's copies, written by letter: no codes.
    if isinstance(drawn_in, PooledFont) and not drawn_in.is_coded():
        return None
    # The file's copies, written by code: the line in their codes.
    if isinstance(drawn_in, PooledFont):
        return drawn_in.coded_runs(plan.text)
    assert_never(drawn_in)


def _spelled(own: PooledFont, text: str) -> str:
    """`text` spelled as the span's own font has it: as typed, composed or in pieces.

    é can be one letter or e and an accent. A trimmed font keeps whichever
    its document used, and macOS pastes in pieces. With none whole in the
    font, composed: the faces we ship draw it that way.
    """
    composed = unicodedata.normalize("NFC", text)
    in_pieces = unicodedata.normalize("NFD", text)
    whole = (spelling for spelling in (text, composed, in_pieces) if not own.missing(spelling))
    return next(whole, composed)


def _in_right_to_left_script(text: str) -> bool:
    """Whether any of `text` is in a script written right to left: Hebrew or Arabic."""
    return any(unicodedata.bidirectional(ch) in _RIGHT_TO_LEFT for ch in text)
