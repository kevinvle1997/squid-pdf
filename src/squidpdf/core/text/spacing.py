"""pdfTeX's fonts have no space: typing one draws the empty glyph, read back as U+0000."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain, repeat

from squidpdf.core.pdf.driver import FontProgram
from squidpdf.core.types import Span, TextPiece


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    offset: float


def lacks_space(font: FontProgram) -> bool:
    return not font.maps(" ")


def _gaps_in(text: str, *, width: float, font: FontProgram, size: float) -> list[float]:
    """Kerning goes in too, so a redraw without it ends where the piece did."""
    spaces = text.count(" ")
    if not spaces:
        return []
    letters_width = font.width(text.replace(" ", ""), size)
    return [(width - letters_width) / spaces / size] * spaces


def span_gaps(span: Span, font: FontProgram) -> list[float]:
    return [
        gap
        for fragment in span.fragments
        for gap in _gaps_in(fragment.text, width=fragment.bbox.width, font=font, size=span.size)
    ]


def usual_gap(pieces: Iterable[TextPiece], *, font: FontProgram) -> float:
    gaps = [
        gap
        for piece in pieces
        for gap in _gaps_in(piece.text, width=piece.box.width, font=font, size=piece.size)
    ]
    return statistics.median(gaps) if gaps else font.advance(" ")


def placed_words(
    text: str,
    *,
    font: FontProgram,
    size: float,
    gaps: Sequence[float],
    usual: float,
    spacing: float,
) -> tuple[list[Word], float]:
    each_gap = chain(gaps, repeat(usual))
    words: list[Word] = []
    pen = 0.0
    for position, word in enumerate(text.split(" ")):
        if position:
            pen += next(each_gap) * size
        if word:
            words.append(Word(word, pen))
            pen += font.width(word, size) + spacing * len(word)
    return words, pen
