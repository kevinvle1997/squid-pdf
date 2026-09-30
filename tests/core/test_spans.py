"""Merging the pieces a page draws into the spans a person edits, without opening a PDF.

Writers split a line into pieces to adjust letter spacing. Which pieces carry
on one span is judged by font, size, paint, baseline and gap, against the thresholds
in core/constants.py; these cases sit well clear of them, so tuning one moves none.
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
    baseline: float = _BASELINE,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    opacity: float = 1.0,
) -> TextPiece:
    """A piece of text from `x0` to `x1` on a baseline, boxed as a font of its size is."""
    box = Rect(x0, baseline - _SIZE * 0.8, x1, baseline + _SIZE * 0.2)
    return TextPiece(text, font, _SIZE, color, opacity, box, (x0, baseline))


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
        (_piece("b", _FIRST_ENDS + 1.0, 125), True),
        (_piece("b", _FIRST_ENDS + 10.0, 125), False),
        (_piece("b", _FIRST_ENDS, 125, font="Times-Bold"), False),
        (_piece("b", _FIRST_ENDS, 125, baseline=_BASELINE - 3), False),
        (_piece("b", _FIRST_ENDS, 125, color=(1.0, 0.0, 0.0)), False),
        (_piece("b", _FIRST_ENDS, 125, opacity=0.6), False),
    ],
    ids=[
        "a small gap",
        "a gap of most of an em: the next column",
        "another font",
        "a raised baseline: a superscript",
        "another colour: a redraw paints a span in one",
        "another opacity",
    ],
)
def test_a_piece_carries_on_the_span_only_close_by_and_in_its_style(then, carries_on):
    first = _piece("a", 72, _FIRST_ENDS)
    expected = ["ab"] if carries_on else ["a", "b"]
    assert_equal(_texts(merge([first, then])), expected, "spans")


def test_blank_runs_are_no_span_and_identical_ones_get_their_own_ids():
    """The same text drawn twice in one place, as table cells can be, is two spans."""
    cell = [_piece("12", 72, 84)]
    blank = [_piece("  ", 72, 80, baseline=_BASELINE + 20)]
    index = build_index([[cell, blank, cell]])
    spans = list(index)
    assert_equal([span.text for span in spans], ["12", "12"], "spans, the blank run left out")
    assert_true(spans[0].id != spans[1].id, f"two spans share the id {spans[0].id}")
