"""What the redaction check calls drawn where it shouldn't be, glyph by glyph (Rule 4).

Drawn by hand: MuPDF's erase can't be made to set glyphs just so. The API's tests hold what
the user is told; these hold how runs of glyphs are told apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import pymupdf
import pytest

from squidpdf.core import DrawnGlyph, DrawnLine, open_pdf
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import GlyphId
from tests.helpers import assert_equal

_LEVEL = (1.0, 0.0)  # a line read left to right
_UP = (0.0, -1.0)  # a line read bottom to top


def _glyph(
    letter: str, x: float, y: float, direction: tuple[float, float] = _LEVEL
) -> DrawnGlyph:
    """A glyph starting at (`x`, `y`), on a line read along `direction`."""
    return DrawnGlyph(letter, GlyphId(0), (x, y), 6.0, direction=direction)


@dataclass(frozen=True, slots=True, eq=False)
class _DrawingDriver:
    """MuPDF's driver over a blank page, drawing whatever `drawn` holds last."""

    driver: PdfDriver
    drawn: list[list[DrawnGlyph]]

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]:
        """The glyphs the test set last."""
        return self.drawn[-1]

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the real driver answers it."""
        return getattr(self.driver, name)


def _drawing(path: str, *, drawn: list[list[DrawnGlyph]]) -> PdfDriver:
    """A driver factory for `open_pdf`: MuPDF's, drawing what `drawn` holds last."""
    return cast(PdfDriver, _DrawingDriver(open_driver(path), drawn))


@pytest.mark.parametrize(
    ("before", "after", "lines", "taken"),
    [
        # A line set off, then the same again: the second is more than went.
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10), _glyph("Z", 100, 100)],
            [_glyph("A", 10, 40), _glyph("B", 16, 40), _glyph("Z", 100, 100)]
            + [_glyph("A", 10, 40), _glyph("B", 16, 40)],
            [],
            "AB",
        ),
        # Two lines drawn one after the other, each set off its own way.
        (
            [_glyph("X", 10, 10), _glyph("Y", 10, 50)],
            [_glyph("X", 10, 20), _glyph("Y", 10, 70)],
            [],
            "",
        ),
        # Two glyphs level, read two ways, so two lines, each set off its own way.
        (
            [_glyph("X", 10, 10), _glyph("Y", 16, 10, direction=_UP)],
            [_glyph("X", 10, 20), _glyph("Y", 26, 20, direction=_UP)],
            [],
            "",
        ),
        # A line's letters in order, each set off its own way: still that line, moved.
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [_glyph("A", 10, 40), _glyph("B", 40, 40)],
            [],
            "",
        ),
        # Letters a line the edits drew has, but elsewhere: no glyph of that line.
        (
            [],
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [DrawnLine("AB", (200, 200), 0)],
            "AB",
        ),
    ],
    ids=[
        "a line set off twice",
        "lines drawn one after another",
        "level, read two ways",
        "a line's letters spread apart",
        "a drawn line's letters elsewhere",
    ],
)
def test_text_moved_is_a_run_of_a_line_drawn_before_and_the_rest_was_to_go(
    tmp_path, before, after, lines, taken
):
    """A run that's some of one line drawn before, in order, set off alike, is text moved."""
    path = str(tmp_path / "blank.pdf")
    blank = pymupdf.open()
    blank.new_page()
    blank.save(path)
    drawn = [before]

    with open_pdf(path, driver_factory=partial(_drawing, drawn=drawn)) as engine:
        kept = engine.redaction.kept_by(0, [])
        drawn.append(after)
        elsewhere = engine.redaction.drawn_elsewhere(0, kept, lines=lines)

    left = "".join(glyph.letter for glyph in elsewhere.taken)
    assert_equal(left, taken, "the letters still drawn the erase was to take")
