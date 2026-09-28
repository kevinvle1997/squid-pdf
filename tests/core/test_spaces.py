"""A font with no space: its words go back where the file put them, and read as words.

pdfTeX's fonts have none; a redraw drew each as the empty glyph, too wide and read as U+0000.
"""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf
import pytest

from squidpdf.core import Span, open_pdf
from tests.core.conftest import GAP_EM, GAPPED_SIZE
from tests.helpers import assert_equal, assert_true

_EM = 1000
_LANDS_PT = 0.1  # how far a redrawn letter may land from where it was
_ADVANCE_EM = 0.6  # A and B, in the fixture's font
_SAME_PT = 0.01  # rounding, far below a pixel

# The gapped fixture's lines, in the order the index reads them (see conftest.py).
_GAPS_OF_THEIR_OWN, _GAPS_IN_ONE_RUN, _LONE_WORD = 0, 1, 2


def _each_letter(blocks: list[dict]) -> Iterator[dict]:
    """Every letter get_text("rawdict") read, in reading order."""
    for block in blocks:
        for line in block["lines"]:
            for piece in line["spans"]:
                yield from piece["chars"]


def _letters(path: str, span: Span) -> list[tuple[str, float, float]]:
    """Each letter on the span's line as MuPDF reads it back: itself, its start and its end."""
    blocks = pymupdf.open(path)[span.page].get_text("rawdict")["blocks"]
    baseline = span.origin[1]
    on_line = (c for c in _each_letter(blocks) if abs(c["origin"][1] - baseline) < _SAME_PT)
    return [(c["c"], c["origin"][0], c["bbox"][2]) for c in on_line]


def _redraw(path: str, out: str, line: int, text: str | None = None) -> Span:
    """One of the fixture's lines redrawn as `text` (its own, if None) and saved to `out`."""
    with open_pdf(path) as engine:
        span = list(engine.index())[line]
        engine.remove([span])
        engine.draw(span, span.text if text is None else text)
        engine.save(out)
    return span


@pytest.mark.parametrize("line", [_GAPS_OF_THEIR_OWN, _GAPS_IN_ONE_RUN])
def test_a_redraw_puts_every_word_back_where_it_was(gapped, tmp_path, line):
    out = str(tmp_path / "same.pdf")
    span = _redraw(gapped, out, line)

    before, after = _letters(gapped, span), _letters(out, span)
    read = "".join(c for c, _start, _end in after)
    assert_equal(read, "".join(c for c, _start, _end in before), "the text read back")
    moved = max(abs(b[1] - a[1]) for a, b in zip(before, after, strict=True))
    assert_true(moved <= _LANDS_PT, f"a letter moved {moved:.2f} pt")


@pytest.mark.parametrize(
    ("line", "text"),
    [
        (_GAPS_OF_THEIR_OWN, "BA AB BA AB"),
        (_GAPS_IN_ONE_RUN, "BA AB BA AB"),
        (_LONE_WORD, "BA AB"),
    ],
)
def test_what_is_measured_is_what_is_drawn(gapped, tmp_path, line, text):
    """The fit check (measure), the browser's live one (widths) and the draw agree on a space.

    A word more than the line had: its space is the page's usual gap.
    """
    out = str(tmp_path / "longer.pdf")
    with open_pdf(gapped) as engine:
        span = list(engine.index())[line]
        measured = engine.measure(span, text)
        widths = engine.widths(span)
    _redraw(gapped, out, line, text)

    drawn_to = _letters(out, span)[-1][2] - span.origin[0]
    assert_true(abs(drawn_to - measured) < _SAME_PT, f"drawn {drawn_to}, measured {measured}")
    words = text.split()
    letters = sum(len(word) for word in words)
    expected = (letters * _ADVANCE_EM + (len(words) - 1) * GAP_EM) * GAPPED_SIZE
    assert_true(abs(measured - expected) < _SAME_PT, f"measured {measured}, not {expected}")
    assert_equal(widths[" "], GAP_EM * _EM, "a space's width, as the browser checks with it")
