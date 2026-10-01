"""Where the words go in a font with no space of its own.

pdfTeX's fonts have none: TeX never draws a space, it moves the pen. Typing one
draws the font's empty glyph instead, wider than the gap and read back as
U+0000. So such a line is drawn word by word, each gap as wide as the file
made it. A gap is how far the pen moves for one space, in ems. Pure
functions: no PDF is opened here.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import chain, repeat

from squidpdf.core.fonts.look_alike import strip_subset
from squidpdf.core.pdf.driver import FontProgram
from squidpdf.core.types import Span, TextPiece

__all__ = [
    "Word",
    "lacks_space",
    "span_gaps",
    "usual_gap",
    "placed_words",
]


@dataclass(frozen=True, slots=True)
class Word:
    """One word of a line, and how far from the line's start it begins, in points."""

    text: str
    offset: float


def lacks_space(font: FontProgram) -> bool:
    """Whether the font has no space of its own, so its words are placed one by one."""
    return not font.maps(" ")


def each_piece(lines: list[list[TextPiece]]) -> Iterator[TextPiece]:
    """Every piece of a page's text, line by line."""
    for line in lines:
        yield from line


def gaps_in(text: str, *, width: float, font: FontProgram, size: float) -> list[float]:
    """The gap for each space in a piece of text `width` wide: one per space.

    What its letters don't fill is gap, shared evenly: the piece doesn't say how
    it was split. Kerning goes in too, so a redraw without it ends where the piece did.
    """
    spaces = text.count(" ")
    if not spaces:
        return []
    letters_width = font.width(text.replace(" ", ""), size)
    return [(width - letters_width) / spaces / size] * spaces


def span_gaps(span: Span, font: FontProgram) -> list[float]:
    """The gap the file left for each space in the span's text, in order."""
    return [
        gap
        for fragment in span.fragments
        for gap in gaps_in(fragment.text, width=fragment.bbox.width, font=font, size=span.size)
    ]


def usual_gap(lines: list[list[TextPiece]], *, font_name: str, font: FontProgram) -> float:
    """The median gap the page leaves for a space in `font_name`, subset prefix aside.

    With no gap to go by, the library's own figure.
    """
    gaps = [
        gap
        for piece in each_piece(lines)
        if strip_subset(piece.font) == font_name
        for gap in gaps_in(piece.text, width=piece.box.width, font=font, size=piece.size)
    ]
    return statistics.median(gaps) if gaps else font.advance(" ")


def placed_words(
    text: str, *, font: FontProgram, size: float, gaps: Sequence[float], usual: float
) -> tuple[list[Word], float]:
    """Each word of `text` where the pen reaches it, and where the pen ends.

    A space draws nothing: the k-th moves the pen `gaps[k]`, any past those `usual`.
    The end is not the last word's: trailing spaces move the pen too.
    """
    each_gap = chain(gaps, repeat(usual))
    words: list[Word] = []
    pen = 0.0
    for position, word in enumerate(text.split(" ")):
        # Every piece after the first had a space before it.
        if position:
            pen += next(each_gap) * size
        # Two spaces in a row leave an empty piece: nothing to draw.
        if word:
            words.append(Word(word, pen))
            pen += font.width(word, size)
    return words, pen
