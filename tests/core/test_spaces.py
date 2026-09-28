"""A font with no space: its words go back where the file put them, and read as words.

pdfTeX's fonts have none; a redraw drew each as the empty glyph, too wide and read as U+0000.
"""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf

from squidpdf.core import Engine, Span, open_pdf
from tests.core.conftest import GAP_EM, GAPPED_SIZE
from tests.helpers import assert_equal, assert_true

_EM = 1000
_LANDS_PT = 0.1  # how far a redrawn letter may land from where it was
_ADVANCE_EM = 0.6  # A and B, in the fixture's font
_SAME_PT = 0.01  # rounding, far below a pixel


def _each_letter(blocks: list[dict]) -> Iterator[dict]:
    """Every letter get_text("rawdict") read, in reading order."""
    for block in blocks:
        for line in block["lines"]:
            for piece in line["spans"]:
                yield from piece["chars"]


def _letters(path: str) -> list[tuple[str, float, float]]:
    """Each letter on the first page as MuPDF reads it back: itself, its start and its end."""
    blocks = pymupdf.open(path)[0].get_text("rawdict")["blocks"]
    return [(c["c"], c["origin"][0], c["bbox"][2]) for c in _each_letter(blocks)]


def _redrawn(path: str, out: str, text: str | None = None) -> tuple[Span, Engine]:
    """The fixture's one span redrawn as `text` (its own, if None) and saved to `out`."""
    engine = open_pdf(path)
    [span] = engine.index()
    engine.remove([span])
    engine.draw(span, span.text if text is None else text)
    engine.save(out)
    return span, engine


def test_a_redraw_puts_every_word_back_where_it_was(gapped, tmp_path):
    out = str(tmp_path / "same.pdf")
    _span, engine = _redrawn(gapped, out)
    engine.close()

    before, after = _letters(gapped), _letters(out)
    read = "".join(c for c, _start, _end in after)
    assert_equal(read, "".join(c for c, _start, _end in before), "the text read back")
    moved = max(abs(b[1] - a[1]) for a, b in zip(before, after, strict=True))
    assert_true(moved <= _LANDS_PT, f"a letter moved {moved:.2f} pt")


def test_what_is_measured_is_what_is_drawn(gapped, tmp_path):
    """The fit check (measure), the browser's live one (widths) and the draw agree on a space.

    A word more than the line had: its space is the line's usual gap.
    """
    out = str(tmp_path / "longer.pdf")
    text = "BA AB BA AB"
    with open_pdf(gapped) as engine:
        [span] = engine.index()
        measured = engine.measure(span, text)
        widths = engine.widths(span)
    _span, engine = _redrawn(gapped, out, text)
    engine.close()

    drawn_to = _letters(out)[-1][2] - span.origin[0]
    assert_true(abs(drawn_to - measured) < _SAME_PT, f"drawn {drawn_to}, measured {measured}")
    expected = (4 * 2 * _ADVANCE_EM + 3 * GAP_EM) * GAPPED_SIZE
    assert_true(abs(measured - expected) < _SAME_PT, f"measured {measured}, not {expected}")
    assert_equal(widths[" "], GAP_EM * _EM, "a space's width, as the browser checks with it")
