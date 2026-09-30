"""Every copy of one font in the file, pooled, so a letter one copy lacks comes from another.

A PDF stores only the letters it used, per copy of a font, and one file can
hold several copies of one face: merged documents, or a copy per page. Pure
functions: the engine finds and opens the copies.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby

from squidpdf.core.constants import SAME_WIDTH
from squidpdf.core.embedded import EmbeddedFont
from squidpdf.core.types import PageFont

__all__ = [
    "FontCopy",
    "PooledFont",
    "font_copy",
    "pooled",
]

_EM = 1000  # widths are given per 1000 em, as PDF font widths are


@dataclass(frozen=True, slots=True)
class FontCopy:
    """One copy of a font in the file, opened, and each letter it really draws."""

    font: PageFont  # where the file keeps it
    embedded: EmbeddedFont
    widths: dict[str, float]  # each letter it really draws, and its width per 1000 em


@dataclass(frozen=True, slots=True)
class PooledFont:
    """The span's own copy of its font and every other copy that agrees with it.

    Each letter comes from the first copy that really draws it. Implements
    `core.driver.FontProgram`, so it measures like one font.
    """

    own: FontCopy  # the copy the span's page uses
    letters: dict[str, FontCopy]  # each letter any copy draws, and the copy it comes from

    def listed_letters(self) -> list[int]:
        """Every letter some copy really draws."""
        return [ord(ch) for ch in self.letters]

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems, in the copy that draws it."""
        return self.copy_for(ch).embedded.program.advance(ch)

    def maps(self, ch: str) -> bool:
        """Whether the copy that draws `ch` has a glyph of its own for it."""
        return self.copy_for(ch).embedded.program.maps(ch)

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points, each stretch measured in its own copy."""
        return sum(
            copy.embedded.program.width(stretch, size) for copy, stretch in self.stretches(text)
        )

    def copy_for(self, ch: str) -> FontCopy:
        """The copy that draws `ch`, or the span's own when none does."""
        # .get: a letter no copy draws is measured in the own copy, as before pooling.
        return self.letters.get(ch, self.own)

    def stretches(self, text: str) -> list[tuple[FontCopy, str]]:
        """`text` split where the copy that draws it changes, in order."""
        return [(copy, "".join(letters)) for copy, letters in groupby(text, key=self.copy_for)]

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that no copy draws, in order, deduped."""
        return [ch for ch in dict.fromkeys(text) if ch not in self.letters]


def font_copy(font: PageFont, embedded: EmbeddedFont) -> FontCopy:
    """A copy, with the width of each letter it really draws, as the page places it."""
    letters = embedded.coverage.drawable()
    coded = embedded.coded
    # Written by code: the page places each letter by the font's width list.
    if coded is not None:
        return FontCopy(font, embedded, {ch: coded.letters[ch].width for ch in letters})
    program = embedded.program
    return FontCopy(font, embedded, {ch: program.advance(ch) * _EM for ch in letters})


def pooled(own: FontCopy, others: Iterable[FontCopy]) -> PooledFont:
    """The own copy's letters, then each other copy's that agrees, in the order given."""
    letters = dict.fromkeys(own.widths, own)
    for other in others:
        if not agrees(other, own=own, letters=letters):
            continue
        for ch in other.widths:
            letters.setdefault(ch, other)  # nearest first, so the nearest to draw it lends it
    return PooledFont(own, letters)


def agrees(other: FontCopy, *, own: FontCopy, letters: dict[str, FontCopy]) -> bool:
    """Whether `other` is the same font as the pool so far, not only the same name.

    The same kind, written the same way (by letter or by code), and every
    letter both draw as wide within SAME_WIDTH. A space isn't compared: a copy
    that never drew one gives its empty glyph's width instead.
    """
    same_kind = other.font.kind == own.font.kind
    same_way = (other.embedded.coded is None) == (own.embedded.coded is None)
    shared = [ch for ch in other.widths if ch in letters and not ch.isspace()]
    same_widths = all(
        abs(other.widths[ch] - letters[ch].widths[ch]) <= SAME_WIDTH for ch in shared
    )
    return same_kind and same_way and same_widths
