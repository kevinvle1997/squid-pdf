"""Drawn by hand: MuPDF's erase can't be made to set glyphs just so."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import pymupdf
import pytest

from squidpdf.core import DrawnGlyph, DrawnLine, new_text, open_pdf
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import GlyphId, Rect
from tests.helpers import assert_equal

_LEVEL = (1.0, 0.0)
_UP = (0.0, -1.0)


def _glyph(
    letter: str, x: float, y: float, direction: tuple[float, float] = _LEVEL
) -> DrawnGlyph:
    return DrawnGlyph(letter, GlyphId(0), (x, y), 6.0, direction=direction)


def _box(glyph: DrawnGlyph) -> Rect:
    (x, y), (along_x, along_y) = glyph.origin, glyph.direction
    # Up from the baseline is a quarter turn from along it, the page's y growing downward.
    up_x, up_y = along_y, -along_x
    xs = [x, x + 6 * along_x, x + 8.4 * up_x, x + 6 * along_x + 8.4 * up_x]
    ys = [y, y + 6 * along_y, y + 8.4 * up_y, y + 6 * along_y + 8.4 * up_y]
    return Rect(min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True, slots=True, eq=False)
class _DrawingDriver:
    driver: PdfDriver
    drawn: list[list[DrawnGlyph]]

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]:
        return self.drawn[-1]

    def drawn_glyph_boxes(self, page: int) -> list[Rect]:
        return [_box(glyph) for glyph in self.drawn[-1]]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _drawing(path: str, *, drawn: list[list[DrawnGlyph]]) -> PdfDriver:
    return cast(PdfDriver, _DrawingDriver(open_driver(path), drawn))


@pytest.mark.parametrize(
    ("before", "after", "lines", "taken"),
    [
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10), _glyph("Z", 100, 100)],
            [_glyph("A", 10, 40), _glyph("B", 16, 40), _glyph("Z", 100, 100)]
            + [_glyph("A", 10, 40), _glyph("B", 16, 40)],
            [],
            "AB",
        ),
        (
            [_glyph("X", 10, 10), _glyph("Y", 10, 50)],
            [_glyph("X", 10, 20), _glyph("Y", 10, 70)],
            [],
            "",
        ),
        (
            [_glyph("X", 10, 10), _glyph("Y", 16, 10, direction=_UP)],
            [_glyph("X", 10, 20), _glyph("Y", 26, 20, direction=_UP)],
            [],
            "",
        ),
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [_glyph("A", 10, 40), _glyph("B", 40, 40)],
            [],
            "",
        ),
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
    elsewhere = _drawn_elsewhere(tmp_path, before, after, lines=lines)

    left = "".join(glyph.letter for glyph in elsewhere.taken)
    assert_equal(left, taken, "the letters still drawn the erase was to take")


def _drawn_elsewhere(tmp_path, before, after, **asked):
    path = str(tmp_path / "blank.pdf")
    blank = pymupdf.open()
    blank.new_page()
    blank.save(path)
    drawn = [before]
    with open_pdf(path, driver_factory=partial(_drawing, drawn=drawn)) as engine:
        kept = engine.redaction.kept_by(0, [])
        drawn.append(after)
        return engine.redaction.drawn_elsewhere(0, kept, **asked)


_REDACTED = new_text(0, origin=(0.0, 10.0), text="Q", size=12.0, font="")


@pytest.mark.parametrize(
    ("before", "after", "words", "found"),
    [
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [_glyph("A", 30, 10), _glyph("B", 36, 10)],
            [],
            ("", "", "moved"),
        ),
        ([_glyph("A", 10, 40)], [_glyph("A", 30, 40)], [], ("", "A", "in_place")),
        (
            [_glyph("A", 10, 10, direction=_UP)],
            [_glyph("A", 10, -10, direction=_UP)],
            [],
            ("", "A", "in_place"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [_glyph("A", 30, 10), _glyph("B", 50, 10)],
            [],
            ("", "AB", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 100, 100), _glyph("B", 16, 10)],
            [_glyph("A", 30, 10), _glyph("Z", 100, 100), _glyph("B", 76, 10)],
            [],
            ("", "AB", "removed"),
        ),
        (
            [_glyph("1", 10, 10), _glyph("2", 16, 10)],
            [_glyph("1", 30, 10), _glyph("2", 36, 10)],
            ["12"],
            ("12", "", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 30, 9)],
            [_glyph("A", 30, 10), _glyph("Z", 30, 9)],
            [],
            ("", "A", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 30, 2)],
            [_glyph("A", 30, 10), _glyph("Z", 30, 2)],
            [],
            ("", "A", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 30, -4)],
            [_glyph("A", 30, 10), _glyph("Z", 30, -4)],
            [],
            ("", "", "moved"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 100, 100), _glyph("B", 16, 10)],
            [_glyph("A", 30, 10), _glyph("Z", 100, 100), _glyph("B", 16, 10)],
            [],
            ("", "A", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 100, 100), _glyph("B", 16, 10)],
            [_glyph("A", 10, 10), _glyph("Z", 100, 100), _glyph("B", 30, 10)],
            [],
            ("", "", "moved"),
        ),
        (
            [_glyph("B", 40, 10), _glyph("Z", 100, 100), _glyph("A", 10, 10)],
            [_glyph("B", 60, 10), _glyph("Z", 100, 100), _glyph("A", 10, 10)],
            [],
            ("", "", "moved"),
        ),
        (
            [
                _glyph("A", 10, 10),
                _glyph("B", 40, 10),
                _glyph("Z", 100, 100),
                _glyph("C", 70, 10),
            ],
            [
                _glyph("A", 10, 10),
                _glyph("B", 40, 10),
                _glyph("Z", 100, 100),
                _glyph("C", 25, 10),
            ],
            [],
            ("", "C", "removed"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("B", 16, 10)],
            [_glyph("A", 590, 10), _glyph("B", 596, 10)],
            [],
            ("", "", "moved"),
        ),
        (
            [_glyph("A", 10, 10), _glyph("Z", 30, 10)],
            [_glyph("A", 30, 10), _glyph("Z", 30, 10)],
            [],
            ("", "A", "removed"),
        ),
        ([_glyph("A", 10, 10)], [_glyph("A", 2000, 10)], [], ("", "", "removed")),
    ],
    ids=[
        "the rest of its line",
        "another line",
        "level, read another way",
        "spread along it",
        "two runs, two ways",
        "the words redacted",
        "onto a glyph a point up",
        "onto the line above",
        "under the line above",
        "set past some left where it was",
        "after some left where it was",
        "drawn right to left",
        "into a gap before some left",
        "some off the page",
        "onto other text",
        "off the page",
    ],
)
def test_the_rest_of_a_redacted_line_is_what_moved_along_it_together(
    tmp_path, before, after, words, found
):
    elsewhere = _drawn_elsewhere(
        tmp_path, before, after, lines=[], rests=[_REDACTED], words=words
    )

    letters = (
        "".join(glyph.letter for glyph in elsewhere.taken),
        "".join(glyph.letter for glyph in elsewhere.moved),
        *elsewhere.rests,
    )
    assert_equal(
        letters, found, "the words still drawn, other text moved, the rest of the line"
    )
