"""The faces we ship, read: which letters each draws, and which one draws a line instead.

No PDF is opened here, and nothing is measured: widths come from the backend,
so a preview matches what it draws.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from squidpdf.core.constants import GLYPH_LIST_RANGES
from squidpdf.core.coverage import Coverage
from squidpdf.core.fonts import broadest, face_bytes
from squidpdf.core.types import Face


@dataclass(frozen=True, slots=True)
class StandIn:
    """A face we ship drawing a line the span's own font can't, less what even it can't draw."""

    face: Face
    text: str  # the line as it's drawn, without the letters left out
    left_out: list[str]  # each once, in the order typed


@cache
def face_coverage(face: Face) -> Coverage:
    """Which letters a face we ship really draws, read from its file once per process."""
    return Coverage(face_bytes(face))


@cache
def face_letters(face: Face) -> tuple[str, ...]:
    """Each letter a face we ship draws within GLYPH_LIST_RANGES, in code point order.

    The letters the browser previews new text with, so the list is kept short;
    letters past those ranges still draw, and the server's fit says so.
    """
    coverage = face_coverage(face)
    return tuple(
        chr(codepoint)
        for start, end in GLYPH_LIST_RANGES
        for codepoint in range(start, end)
        if coverage.covers(chr(codepoint))
    )


def stand_in(look_alike: Face, text: str) -> StandIn:
    """The face that draws `text` in place of a font whose look-alike is `look_alike`.

    The look-alike when it has every letter; otherwise whichever of it and the
    broadest face we ship leaves out fewer, the look-alike on a tie. One face
    for the whole line: two would look like a mistake.
    """
    runs = [_run(face, text) for face in (look_alike, broadest(look_alike))]
    return min(runs, key=lambda run: len(run.left_out))  # min keeps the first of a tie


def _run(face: Face, text: str) -> StandIn:
    """`text` drawn in `face`: what it draws, and what it leaves out."""
    left_out = face_coverage(face).missing(text)
    kept = "".join(ch for ch in text if ch not in left_out)
    return StandIn(face, kept, left_out)
