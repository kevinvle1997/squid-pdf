"""Every copy of one font in the file, pooled, so a letter one copy lacks comes from another.

A PDF stores only the letters it used, per copy, and one file can hold several copies of a
face (merged documents, a copy per page), which Google's can join. `core.fonts.document`
finds and opens them; the pool takes each in only when a letter needs it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import chain, groupby
from typing import Protocol

from squidpdf.core.app.message import Message
from squidpdf.core.constants import (
    KEYBOARD_RANGES,
    LENT_COPIES_KEPT,
    SAME_FONT_SHARED,
    SAME_WIDTH,
)
from squidpdf.core.fonts.coverage import Coverage, coverage_of
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable
from squidpdf.core.types import EM, CodedFont, FontCode, PageFont

# The copies that may lend the own copy letters, in lending order, given those lent so far
# (they decide whether a later one is worth opening); one that can't be had comes as why.
type _Lenders = Callable[[Mapping[str, FontCopy]], Iterable[FontCopy | FontUnusable]]


@dataclass(frozen=True, slots=True)
class Lent:
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
    lent: Lent | None = None  # set when it's lent from outside the file, as Google's copy is


class Lender(Protocol):
    """A source of copies from outside the file, asked in order for letters the file's lack."""

    def copy_of(self, own: FontCopy) -> FontCopy | None:
        """Its copy of the own copy's font, to be checked like any other.

        None when it has none and that's no news (nothing attached); raises
        FontUnusable, saying why, when it has none for a reason worth saying.
        """
        ...


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

    Each letter comes from the first copy, in lending order, that draws it; a copy is taken in
    only when a letter the ones so far lack is asked for. Measures as one `FontProgram`.
    `eq=False` is needed: it's in a dict key (`_PageFacts.usual_gaps`), so it hashes as itself.
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

    def every_letter(self) -> dict[str, FontCopy]:
        """Each letter any copy draws, and the copy it comes from, every copy taken in first."""
        for other in self.lenders:
            self._take_in(other)
        return self.taken_in

    def is_coded(self) -> bool:
        """Whether it's written by code, not by letter: every copy in it is, as the own copy."""
        return self.own.embedded.coded is not None

    def listed_letters(self) -> list[int]:
        """Every letter some copy really draws."""
        return [ord(ch) for ch in self.every_letter()]

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

    def coded_runs(self, text: str, *, drawn: Mapping[str, FontCode]) -> list[CodedRun] | None:
        """`text` split by copy, each with its codes; None when a copy is written by letter.

        The own copy writes a letter of `drawn` in the code given, as the span does.
        """
        runs: list[CodedRun] = []
        for run in self.runs(text):
            coded = run.copy.embedded.coded
            # Written by letter: there are no codes to write it in.
            if coded is None:
                return None
            # The own copy draws each letter as the span does; another has codes of its own.
            if run.copy is self.own:
                coded = coded.preferring(drawn)
            runs.append(CodedRun(run.text, run.copy, coded))
        return runs

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that no copy draws, in order, deduped."""
        return [ch for ch in dict.fromkeys(text) if self._lender_of(ch) is None]

    def why_missing(self, text: str) -> Message:
        """Why the pool can't draw all of `text`.

        A copy turned away that draws a missing letter says why; else one from outside the file
        that couldn't be had, after the file's own reason; else no copy has the letter.
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
        why = why_turned_away(other, own=self.own, letters=self.taken_in)
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


def font_copy(font: PageFont, embedded: EmbeddedFont, coverage: Coverage) -> FontCopy:
    """A copy, with the width of each letter `coverage` says it draws, as the page places it."""
    letters = coverage.drawable()
    coded = embedded.coded
    # Written by code: the page places each letter by the font's width list.
    if coded is not None:
        return FontCopy(font, embedded, {ch: coded.letters[ch].width for ch in letters})
    program = embedded.program
    return FontCopy(font, embedded, {ch: program.advance(ch) * EM for ch in letters})


def copy_source(copy: FontCopy) -> str:
    """What tells one copy from another: where it was lent from, or its object and name."""
    if copy.lent is not None:
        return copy.lent.source
    return f"{copy.font.xref} {copy.font.name}"


def lacks_a_keyboard_letter(letters: Mapping[str, FontCopy]) -> bool:
    """Whether some letter in KEYBOARD_RANGES is one no copy lends in `letters`."""
    keyboard = (chr(code) for block in KEYBOARD_RANGES for code in block)
    return any(ch not in letters for ch in keyboard)


@dataclass(slots=True)
class _KeptWidths:
    """The letters and widths of each copy from outside the file, by its digest, oldest first.

    Finding which letters draw is slow, so a process keeps them.
    It keeps only the latest LENT_COPIES_KEPT, so a long-lived worker stays small.
    """

    by_digest: dict[bytes, dict[str, float]] = field(default_factory=dict, repr=False)

    def widths(self, digest: bytes, make: Callable[[], dict[str, float]]) -> dict[str, float]:
        """The widths of the copy `digest` names, measured on first use."""
        if digest in self.by_digest:
            return self.by_digest[digest]
        # Full: forget the one kept longest.
        if len(self.by_digest) >= LENT_COPIES_KEPT:
            del self.by_digest[next(iter(self.by_digest))]
        widths = self.by_digest[digest] = make()
        return widths

    def forget(self) -> None:
        """Forget every copy's widths, as a fresh record."""
        self.by_digest.clear()


# This process's widths of the copies from outside the file: the user's and Google's.
_kept_widths = _KeptWidths()


def lent_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Each letter a copy from outside draws that the browser can preview, per 1000 em.

    Kept by a digest of the bytes, not the name: another file can come under the same name.
    """
    digest = hashlib.sha256(embedded.file).digest()
    return _kept_widths.widths(digest, partial(_previewed_widths, embedded))


def _previewed_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Each letter the copy draws that the browser can preview, per 1000 em, read from its file.

    Only previewed letters are checked for a shape: a large font holds far more outlines.
    """
    previewed = coverage_of(embedded.file).previewed_letters()
    return {ch: embedded.program.advance(ch) * EM for ch in previewed}


def why_unlike_listed(copy: FontCopy, listed: Mapping[str, float] | None) -> Message | None:
    """Why `copy` isn't the font a width list describes; None when it is.

    The same rule as `why_turned_away`: at least SAME_FONT_SHARED letters
    shared, each as wide within SAME_WIDTH. No list: nothing to vouch for it.
    """
    if listed is None:
        return Message("font_no_width_list")
    shared = [ch for ch in copy.widths if ch in listed and not ch.isspace()]
    # Too few in common: nothing to vouch for it.
    if len(shared) < SAME_FONT_SHARED:
        return Message("copy_too_few_shared")
    widths_differ = any(abs(copy.widths[ch] - listed[ch]) > SAME_WIDTH for ch in shared)
    # A letter both have is another width: a different font under the same name.
    if widths_differ:
        return Message("copy_other_widths")
    return None


def why_turned_away(
    other: FontCopy, *, own: FontCopy, letters: Mapping[str, FontCopy]
) -> Message | None:
    """Why `other` isn't the same font as the pool so far, only the same name; None when it is.

    The same font is the same kind, written the same way (by letter or by code), shares at
    least SAME_FONT_SHARED letters, and draws those both have within SAME_WIDTH. A space isn't
    compared: a copy that never drew one gives its empty glyph's width.
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
