"""Every copy of one font in the file, pooled, so a letter one copy lacks comes from another.

A PDF stores only the letters it used, per copy of a font, and one file can
hold several copies of one face: merged documents, or a copy per page, and
Google's copy of it can join them. Pure functions: `core.document_fonts` finds
and opens the copies.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import groupby

from squidpdf.core.constants import (
    GLYPH_LIST_RANGES,
    KEYBOARD_RANGES,
    SAME_FONT_SHARED,
    SAME_WIDTH,
)
from squidpdf.core.embedded import EmbeddedFont
from squidpdf.core.google import GoogleFile
from squidpdf.core.message import Message
from squidpdf.core.types import CodedFont, PageFont

__all__ = [
    "FontCopy",
    "CodedStretch",
    "TurnedAway",
    "PooledFont",
    "font_copy",
    "google_copy",
    "copy_source",
    "lacks_a_keyboard_letter",
    "pooled",
]

_EM = 1000  # widths are given per 1000 em, as PDF font widths are


@dataclass(frozen=True, slots=True)
class FontCopy:
    """One copy of a font in the file, opened, and each letter it really draws."""

    font: PageFont  # where the file keeps it
    embedded: EmbeddedFont
    widths: dict[str, float]  # each letter it really draws, and its width per 1000 em
    google: GoogleFile | None = None  # set when it's Google's copy, not one in the file


@dataclass(frozen=True, slots=True)
class CodedStretch:
    """Letters drawn in one copy of a font written by code, and the codes it's written in."""

    text: str
    copy: FontCopy
    coded: CodedFont
    own: bool  # the span's own copy, whose name on the page is kept


@dataclass(frozen=True, slots=True)
class TurnedAway:
    """A copy by the font's name that lends nothing, and why it isn't the same font."""

    copy: FontCopy
    why: Message


@dataclass(frozen=True, slots=True)
class PooledFont:
    """The span's own copy of its font, every copy that's the same font, and those turned away.

    Each letter comes from the first copy that really draws it. Implements
    `core.driver.FontProgram`, so it measures like one font.
    """

    own: FontCopy  # the copy the span's page uses
    letters: dict[str, FontCopy]  # each letter any copy draws, and the copy it comes from
    # Copies with the font's name that aren't the same font, and why.
    turned_away: list[TurnedAway] = field(default_factory=list)

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
            copy.embedded.program.width(stretch_text, size)
            for copy, stretch_text in self.stretches(text)
        )

    def copy_for(self, ch: str) -> FontCopy:
        """The copy that draws `ch`, or the span's own when none does."""
        # .get: a letter no copy draws is measured in the own copy, as before pooling.
        return self.letters.get(ch, self.own)

    def stretches(self, text: str) -> list[tuple[FontCopy, str]]:
        """`text` split where the copy that draws it changes, in order."""
        return [(copy, "".join(letters)) for copy, letters in groupby(text, key=self.copy_for)]

    def coded_stretches(self, text: str) -> list[CodedStretch] | None:
        """`text` split by copy, each with its codes; None when a copy is written by letter."""
        stretches: list[CodedStretch] = []
        for copy, stretch_text in self.stretches(text):
            coded = copy.embedded.coded
            # Written by letter: there are no codes to write it in.
            if coded is None:
                return None
            own = copy.font.xref == self.own.font.xref
            stretches.append(CodedStretch(stretch_text, copy, coded, own))
        return stretches

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that no copy draws, in order, deduped."""
        return [ch for ch in dict.fromkeys(text) if ch not in self.letters]

    def why_missing(self, text: str) -> Message:
        """Why the pool can't draw all of `text`.

        A copy turned away that draws a missing letter says why it was; else
        no copy of the font has the letter.
        """
        missing = self.missing(text)
        turned_away = (
            turned.why
            for turned in self.turned_away
            if any(ch in turned.copy.widths for ch in missing)
        )
        return next(turned_away, Message("font_lacks_letters"))


def font_copy(font: PageFont, embedded: EmbeddedFont) -> FontCopy:
    """A copy, with the width of each letter it really draws, as the page places it."""
    letters = embedded.coverage.drawable()
    coded = embedded.coded
    # Written by code: the page places each letter by the font's width list.
    if coded is not None:
        return FontCopy(font, embedded, {ch: coded.letters[ch].width for ch in letters})
    program = embedded.program
    return FontCopy(font, embedded, {ch: program.advance(ch) * _EM for ch in letters})


def google_copy(own: FontCopy, embedded: EmbeddedFont, file: GoogleFile) -> FontCopy:
    """Google's copy of the own copy's font, lending only letters the browser can preview.

    Stands for the same font as the own copy, so it's checked like any other copy.
    """
    program = embedded.program
    letters = [ch for ch in embedded.coverage.drawable() if in_glyph_list(ch)]
    widths = {ch: program.advance(ch) * _EM for ch in letters}
    return FontCopy(own.font, embedded, widths, file)


def in_glyph_list(ch: str) -> bool:
    """Whether `ch` is in GLYPH_LIST_RANGES, the letters the browser is sent widths for."""
    return any(start <= ord(ch) < end for start, end in GLYPH_LIST_RANGES)


def copy_source(copy: FontCopy) -> str:
    """What tells one copy from another: Google's file, or its object and name in the file."""
    if copy.google is not None:
        return copy.google.source
    return f"{copy.font.xref} {copy.font.name}"


def lacks_a_keyboard_letter(pool: PooledFont) -> bool:
    """Whether some letter in KEYBOARD_RANGES is one no copy in the pool draws."""
    keyboard = (chr(code) for start, end in KEYBOARD_RANGES for code in range(start, end))
    return any(ch not in pool.letters for ch in keyboard)


def pooled(own: FontCopy, others: Iterable[FontCopy]) -> PooledFont:
    """The own copy's letters, then each same-font copy's, in the order given."""
    letters = dict.fromkeys(own.widths, own)
    turned_away: list[TurnedAway] = []
    for other in others:
        why = why_turned_away(other, own=own, letters=letters)
        # Only the same name: it lends nothing, and keeps why for the report.
        if why is not None:
            turned_away.append(TurnedAway(other, why))
            continue
        for ch in other.widths:
            letters.setdefault(ch, other)  # nearest first, so the nearest to draw it lends it
    return PooledFont(own, letters, turned_away)


def why_turned_away(
    other: FontCopy, *, own: FontCopy, letters: dict[str, FontCopy]
) -> Message | None:
    """Why `other` isn't the same font as the pool so far, only the same name; None when it is.

    The same font is the same kind, written the same way (by letter or by
    code), shares at least SAME_FONT_SHARED letters, and draws every letter
    both have as wide within SAME_WIDTH. A space isn't compared: a copy that
    never drew one gives its empty glyph's width instead.
    """
    same_kind = other.font.kind == own.font.kind
    same_way = (other.embedded.coded is None) == (own.embedded.coded is None)
    # Another kind of font, or written the other way: its letters can't mix with ours.
    if not (same_kind and same_way):
        return Message("copy_stored_apart")
    shared = [ch for ch in other.widths if ch in letters and not ch.isspace()]
    # Too few in common: nothing to vouch for it, so it lends nothing.
    if len(shared) < SAME_FONT_SHARED:
        return Message("copy_too_few_shared")
    widths_differ = any(
        abs(other.widths[ch] - letters[ch].widths[ch]) > SAME_WIDTH for ch in shared
    )
    # A letter both draw is another width: a different font under the same name.
    if widths_differ:
        return Message("copy_other_widths")
    return None
