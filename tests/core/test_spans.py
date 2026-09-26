"""Merging the pieces a page draws into the spans a person edits, without opening a PDF.

Writers split a line into pieces to adjust letter spacing. Which pieces carry
on one span is judged by font, size, baseline and gap, against the thresholds
in core/constants.py; these cases sit clearly either side of each.
"""

from __future__ import annotations

import pytest

from squidpdf.core.spans import build_index, merge
from squidpdf.core.types import Rect, TextPiece
from tests.helpers import assert_equal, assert_true

_SIZE = 11.0
_BASELINE = 100.0
_FIRST_ENDS = 110.0  # where the first piece of each pair ends


def _piece(
    text: str,
    x0: float,
    x1: float,
    *,
    font: str = "Times-Roman",
    size: float = _SIZE,
    baseline: float = _BASELINE,
) -> TextPiece:
    """A piece of text from `x0` to `x1` on a baseline, boxed as a font of its size is."""
    box = Rect(x0, baseline - size * 0.8, x1, baseline + size * 0.2)
    return TextPiece(text, font, size, (0.0, 0.0, 0.0), box, (x0, baseline))


def _texts(groups: list[list[TextPiece]]) -> list[str]:
    """Each merged group's text, as its span reads."""
    return ["".join(piece.text for piece in group) for group in groups]


def test_pieces_split_for_spacing_merge_back_into_one_span():
    """A kerned word in two pieces, then the next word: one span, as a person reads it."""
    pieces = [_piece("Deliv", 72, 96), _piece("ery", 96.3, 110), _piece(" begins", 110, 140)]
    assert_equal(_texts(merge(pieces)), ["Delivery begins"], "spans")


@pytest.mark.parametrize(
    ("then", "carries_on"),
    [
        (_piece("b", _FIRST_ENDS + 3.8, 125), True),
        (_piece("b", _FIRST_ENDS + 3.9, 125), False),
        (_piece("b", _FIRST_ENDS - 1.0, 125), True),
        (_piece("b", _FIRST_ENDS - 1.1, 125), False),
        (_piece("b", _FIRST_ENDS, 125, font="Times-Bold"), False),
        (_piece("b", _FIRST_ENDS, 125, size=_SIZE + 0.05), True),
        (_piece("b", _FIRST_ENDS, 125, size=_SIZE + 0.2), False),
        (_piece("b", _FIRST_ENDS, 125, baseline=_BASELINE + 0.5), True),
        (_piece("b", _FIRST_ENDS, 125, baseline=_BASELINE - 3), False),
    ],
    ids=[
        "a gap within a third of the size",
        "a gap past it: the next column",
        "kerning pulling it back 1 pt",
        "back further: it overlaps",
        "another font",
        "a size within rounding",
        "another size",
        "a baseline within rounding",
        "a raised baseline: a superscript",
    ],
)
def test_a_piece_carries_on_the_span_only_close_by_and_in_its_style(then, carries_on):
    first = _piece("a", 72, _FIRST_ENDS)
    expected = ["ab"] if carries_on else ["a", "b"]
    assert_equal(_texts(merge([first, then])), expected, "spans")


def test_a_span_takes_its_box_from_all_its_pieces_and_its_style_from_the_first():
    pieces = [_piece("Deliv", 72.004, 96), _piece("ery", 96.3, 110.126, size=_SIZE + 0.05)]
    [span] = build_index([[pieces]])
    assert_equal(span.text, "Delivery", "its text")
    assert_equal((span.bbox.x0, span.bbox.x1), (72.0, 110.13), "its box, to 2 decimals")
    assert_equal(span.origin, (72.0, _BASELINE), "where it starts, on its baseline")
    assert_equal(span.size, _SIZE, "its size, the first piece's")
    assert_equal(len(span.fragments), 2, "the pieces it keeps, to redraw from")


def test_blank_runs_are_no_span_and_identical_ones_get_their_own_ids():
    """The same text drawn twice in one place, as table cells can be, is two spans."""
    cell = [_piece("12", 72, 84)]
    blank = [_piece("  ", 72, 80, baseline=_BASELINE + 20)]
    index = build_index([[cell, blank, cell]])
    spans = list(index)
    assert_equal([span.text for span in spans], ["12", "12"], "spans, the blank run left out")
    assert_true(spans[0].id != spans[1].id, f"two spans share the id {spans[0].id}")
