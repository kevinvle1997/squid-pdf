"""Every copy of one font in the file, pooled, so a letter one copy lacks comes from another.

A PDF stores only the letters it used, per copy of a font, and one file can
hold several copies of one face: merged documents, or a copy per page, and
Google's copy of it can join them. `core.fonts.document` finds and opens the
copies; the pool takes each in only when a letter needs it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import chain, groupby

from squidpdf.core.app.message import Message
from squidpdf.core.constants import (
    GLYPH_LIST_RANGES,
    KEYBOARD_RANGES,
    SAME_FONT_SHARED,
    SAME_WIDTH,
)
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable
from squidpdf.core.fonts.google import GoogleFile
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.types import EM, CodedFont, PageFont

# How many of Google's copies a process keeps the letters of: each is a few tens of KB.
_GOOGLE_COPIES_KEPT = 32

# The copies that may lend the own copy letters, in the order they lend, given the
# letters lent so far: whether a later one is worth opening can depend on them. A
# copy from outside the file that can't be had comes as why, to be said.
type _Lenders = Callable[[Mapping[str, FontCopy]], Iterable[FontCopy | FontUnusable]]


@dataclass(slots=True)
class _KeptWidths:
    """Each Google copy's letters and widths, by a digest of the file, oldest first.

    A process keeps them (`_kept_widths`): finding which letters really draw is
    slow, and the same bytes always give the same answer.
    It keeps the latest _GOOGLE_COPIES_KEPT, so a long-lived worker stays small.
    """

    by_digest: dict[bytes, dict[str, float]] = field(default_factory=dict, repr=False)

    def widths(self, digest: bytes, make: Callable[[], dict[str, float]]) -> dict[str, float]:
        """The widths of the copy `digest` names, measured on first use."""
        # Kept already: this process has measured these bytes.
        if digest in self.by_digest:
            return self.by_digest[digest]
        # Full: forget the one kept longest.
        if len(self.by_digest) >= _GOOGLE_COPIES_KEPT:
            del self.by_digest[next(iter(self.by_digest))]
        widths = self.by_digest[digest] = make()
        return widths

    def forget(self) -> None:
        """Forget every copy's widths, as a fresh record."""
        self.by_digest.clear()


# This process's widths of Google's copies.
_kept_widths = _KeptWidths()


@dataclass(frozen=True, slots=True)
class _Lent:
    """Where a copy lent from outside the file came from, and what the reader calls it."""

    source: str  # what it's cached and added to a page as, e.g. Google's path and weight
    name: str  # the font's own name, "Poppins-Bold", for a sentence that names it


@dataclass(frozen=True, slots=True)
class FontCopy:
    """One copy of a font in the file, opened, and each letter it really draws."""

    font: PageFont  # where the file keeps it
    embedded: EmbeddedFont
    # Each letter it really draws, and its width per 1000 em.
    widths: dict[str, float] = field(repr=False)
    lent: _Lent | None = None  # set when it's lent from outside the file, as Google's copy is


@dataclass(frozen=True, slots=True)
class _CopyRun:
    """A run of a line: letters drawn in one copy of a font."""

    text: str
    copy: FontCopy


@dataclass(frozen=True, slots=True)
class CodedRun:
    """A run of a line in one copy of a font written by code, and the codes it's written in."""

    text: str
    copy: FontCopy
    coded: CodedFont


@dataclass(frozen=True, slots=True)
class _TurnedAway:
    """A copy by the font's name that lends nothing, and why it isn't the same font."""

    copy: FontCopy
    why: Message


@dataclass(frozen=True, slots=True, eq=False)
class PooledFont:
    """The span's own copy of its font, and the copies of the same font, taken in as needed.

    Each letter comes from the first copy, in the order they lend, that really
    draws it. A copy is taken in only when a letter the ones so far lack is
    asked for, so a line the own copy draws opens no other. Implements
    `core.pdf.driver.FontProgram`, so it measures like one font. Made by
    `pooled_font`; its fields are never replaced, only filled. `eq=False` is
    needed, not only tidy: it's part of a dict key (`_PageFacts.usual_gaps`),
    so it hashes as itself.
    """

    own: FontCopy  # the copy the span's page uses
    # Each letter a copy taken in draws, and the copy it comes from.
    taken_in: dict[str, FontCopy] = field(repr=False)
    # The copies not taken in yet, each opened only when it's reached.
    lenders: Iterator[FontCopy | FontUnusable] = field(repr=False)
    # Copies taken in with the font's name that aren't the same font, and why.
    turned_away: list[_TurnedAway] = field(default_factory=list)
    # Why each copy from outside the file asked for couldn't be had, in order.
    not_lent: list[Message] = field(default_factory=list)

    @property
    def letters(self) -> dict[str, FontCopy]:
        """Each letter any copy draws, and the copy it comes from, every copy taken in first."""
        for other in self.lenders:
            self._take_in(other)
        return self.taken_in

    def is_coded(self) -> bool:
        """Whether it's written by code, not by letter: every copy in it is, as the own copy."""
        return self.own.embedded.coded is not None

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
        """How wide `text` is at `size` points, each run measured in its own copy."""
        return sum(run.copy.embedded.program.width(run.text, size) for run in self.runs(text))

    def copy_for(self, ch: str) -> FontCopy:
        """The copy that draws `ch`, or the span's own when none does."""
        lender = self._lender_of(ch)
        # No copy draws it: it's measured in the own copy, as before pooling.
        if lender is None:
            return self.own
        return lender

    def runs(self, text: str) -> list[_CopyRun]:
        """`text` split where the copy that draws it changes, in order."""
        return [
            _CopyRun("".join(letters), copy)
            for copy, letters in groupby(text, key=self.copy_for)
        ]

    def coded_runs(self, text: str) -> list[CodedRun] | None:
        """`text` split by copy, each with its codes; None when a copy is written by letter."""
        runs: list[CodedRun] = []
        for run in self.runs(text):
            coded = run.copy.embedded.coded
            # Written by letter: there are no codes to write it in.
            if coded is None:
                return None
            runs.append(CodedRun(run.text, run.copy, coded))
        return runs

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that no copy draws, in order, deduped."""
        return [ch for ch in dict.fromkeys(text) if self._lender_of(ch) is None]

    def why_missing(self, text: str) -> Message:
        """Why the pool can't draw all of `text`.

        A copy turned away that draws a missing letter says why it was; else a
        copy from outside the file that couldn't be had says why, after the
        file's own reason (Google's `google_*` sentences); else no copy of the
        font has the letter.
        """
        # A missing letter took every copy in, so every copy turned away is known.
        missing = self.missing(text)
        turned_away = (
            turned.why
            for turned in self.turned_away
            if any(ch in turned.copy.widths for ch in missing)
        )
        return next(chain(turned_away, self.not_lent, [Message("font_lacks_letters")]))

    def _lender_of(self, ch: str) -> FontCopy | None:
        """The copy that draws `ch`, taking copies in until one does; None when none does."""
        while ch not in self.taken_in:
            other = next(self.lenders, None)
            # Every copy is in, and none draws it.
            if other is None:
                return None
            self._take_in(other)
        return self.taken_in[ch]

    def _take_in(self, other: FontCopy | FontUnusable) -> None:
        """Add the letters `other` draws that the pool lacks, if it's the same font.

        A copy from outside the file that couldn't be had lends nothing: why is kept.
        """
        # No copy to be had: it lends nothing, and keeps why for the report.
        if isinstance(other, FontUnusable):
            self.not_lent.append(other.reason)
            return
        why = _why_turned_away(other, own=self.own, letters=self.taken_in)
        # Only the same name: it lends nothing, and keeps why for the report.
        if why is not None:
            self.turned_away.append(_TurnedAway(other, why))
            return
        for ch in other.widths:
            self.taken_in.setdefault(ch, other)  # in lending order: the first to draw it lends


def pooled_font(own: FontCopy, lenders: _Lenders) -> PooledFont:
    """The own copy's letters, and the copies to take in, in order, for any it lacks.

    `lenders` is handed the pool's letters, which grow as copies are taken in.
    """
    taken_in = dict.fromkeys(own.widths, own)
    return PooledFont(own, taken_in, iter(lenders(taken_in)))


def font_copy(font: PageFont, embedded: EmbeddedFont) -> FontCopy:
    """A copy, with the width of each letter it really draws, as the page places it."""
    letters = embedded.coverage.drawable()
    coded = embedded.coded
    # Written by code: the page places each letter by the font's width list.
    if coded is not None:
        return FontCopy(font, embedded, {ch: coded.letters[ch].width for ch in letters})
    program = embedded.program
    return FontCopy(font, embedded, {ch: program.advance(ch) * EM for ch in letters})


def google_copy(own: FontCopy, embedded: EmbeddedFont, file: GoogleFile) -> FontCopy:
    """Google's copy of the own copy's font, lending only letters the browser can preview.

    Stands for the same font as the own copy, so it's checked like any other copy.
    """
    lent = _Lent(file.source, strip_subset(own.font.name))
    return FontCopy(own.font, embedded, _google_widths(embedded), lent)


def _google_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Each letter Google's copy draws that the browser can preview, and its width per 1000 em.

    Worked out once per process for each file (`_kept_widths`). By the bytes, not
    the file's name: a test can hand in another font under it.
    """
    digest = hashlib.sha256(embedded.file).digest()
    return _kept_widths.widths(digest, partial(_measured_widths, embedded))


def _measured_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Each letter Google's copy draws that the browser can preview, measured in it."""
    program = embedded.program
    letters = [ch for ch in embedded.coverage.drawable() if _in_glyph_list(ch)]
    return {ch: program.advance(ch) * EM for ch in letters}


def _in_glyph_list(ch: str) -> bool:
    """Whether `ch` is in GLYPH_LIST_RANGES, the letters the browser is sent widths for."""
    return any(ord(ch) in block for block in GLYPH_LIST_RANGES)


def copy_source(copy: FontCopy) -> str:
    """What tells one copy from another: where it was lent from, or its object and name."""
    if copy.lent is not None:
        return copy.lent.source
    return f"{copy.font.xref} {copy.font.name}"


def lacks_a_keyboard_letter(letters: Mapping[str, FontCopy]) -> bool:
    """Whether some letter in KEYBOARD_RANGES is one no copy lends in `letters`."""
    keyboard = (chr(code) for block in KEYBOARD_RANGES for code in block)
    return any(ch not in letters for ch in keyboard)


def _why_turned_away(
    other: FontCopy, *, own: FontCopy, letters: Mapping[str, FontCopy]
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
