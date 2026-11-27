from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import chain, groupby
from typing import Protocol

import squidpdf.core.constants as constants
from squidpdf.core.app.message import Message
from squidpdf.core.constants import KEYBOARD_RANGES
from squidpdf.core.fonts.coverage import Coverage, coverage_of
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable
from squidpdf.core.types import EM, CodedFont, FontCode, PageFont

type _Lenders = Callable[[Mapping[str, FontCopy]], Iterable[FontCopy | FontUnusable]]


@dataclass(frozen=True, slots=True)
class Lent:
    source: str
    name: str


@dataclass(frozen=True, slots=True)
class FontCopy:
    font: PageFont
    embedded: EmbeddedFont
    widths: dict[str, float] = field(repr=False)
    lent: Lent | None = None


class Lender(Protocol):
    def copy_of(self, own: FontCopy) -> FontCopy | None: ...


@dataclass(frozen=True, slots=True)
class _CopyRun:
    text: str
    copy: FontCopy


@dataclass(frozen=True, slots=True)
class CodedRun:
    text: str
    copy: FontCopy
    coded: CodedFont


@dataclass(frozen=True, slots=True)
class _TurnedAway:
    copy: FontCopy
    why: Message


@dataclass(frozen=True, slots=True, eq=False)
class PooledFont:
    """`eq=False`: it's in a dict key (`_PageFacts.usual_gaps`), so it hashes as itself."""

    own: FontCopy
    taken_in: dict[str, FontCopy] = field(repr=False)
    lenders: Iterator[FontCopy | FontUnusable] = field(repr=False)
    turned_away: list[_TurnedAway] = field(default_factory=list)
    not_lent: list[Message] = field(default_factory=list)

    def every_letter(self) -> dict[str, FontCopy]:
        for other in self.lenders:
            self._take_in(other)
        return self.taken_in

    def is_coded(self) -> bool:
        return self.own.embedded.coded is not None

    def listed_letters(self) -> list[int]:
        return [ord(ch) for ch in self.every_letter()]

    def advance(self, ch: str) -> float:
        return self.copy_for(ch).embedded.program.advance(ch)

    def maps(self, ch: str) -> bool:
        return self.copy_for(ch).embedded.program.maps(ch)

    def width(self, text: str, size: float) -> float:
        return sum(run.copy.embedded.program.width(run.text, size) for run in self.runs(text))

    def copy_for(self, ch: str) -> FontCopy:
        lender = self._lender_of(ch)
        if lender is None:
            return self.own
        return lender

    def runs(self, text: str) -> list[_CopyRun]:
        return [
            _CopyRun("".join(letters), copy)
            for copy, letters in groupby(text, key=self.copy_for)
        ]

    def coded_runs(self, text: str, *, drawn: Mapping[str, FontCode]) -> list[CodedRun] | None:
        runs: list[CodedRun] = []
        for run in self.runs(text):
            coded = run.copy.embedded.coded
            if coded is None:
                return None
            if run.copy is self.own:
                coded = coded.preferring(drawn)
            runs.append(CodedRun(run.text, run.copy, coded))
        return runs

    def missing(self, text: str) -> list[str]:
        return [ch for ch in dict.fromkeys(text) if self._lender_of(ch) is None]

    def why_missing(self, text: str) -> Message:
        # A missing letter took every copy in, so every copy turned away is known.
        missing = self.missing(text)
        turned_away = (
            turned.why
            for turned in self.turned_away
            if any(ch in turned.copy.widths for ch in missing)
        )
        return next(chain(turned_away, self.not_lent, [Message("font_lacks_letters")]))

    def _lender_of(self, ch: str) -> FontCopy | None:
        while ch not in self.taken_in:
            other = next(self.lenders, None)
            if other is None:
                return None
            self._take_in(other)
        return self.taken_in[ch]

    def _take_in(self, other: FontCopy | FontUnusable) -> None:
        if isinstance(other, FontUnusable):
            self.not_lent.append(other.reason)
            return
        why = why_turned_away(other, own=self.own, letters=self.taken_in)
        if why is not None:
            self.turned_away.append(_TurnedAway(other, why))
            return
        for ch in other.widths:
            self.taken_in.setdefault(ch, other)


def pooled_font(own: FontCopy, lenders: _Lenders) -> PooledFont:
    taken_in = dict.fromkeys(own.widths, own)
    return PooledFont(own, taken_in, iter(lenders(taken_in)))


def font_copy(font: PageFont, embedded: EmbeddedFont, coverage: Coverage) -> FontCopy:
    letters = coverage.drawable()
    coded = embedded.coded
    if coded is not None:
        return FontCopy(font, embedded, {ch: coded.letters[ch].width for ch in letters})
    program = embedded.program
    return FontCopy(font, embedded, {ch: program.advance(ch) * EM for ch in letters})


def copy_source(copy: FontCopy) -> str:
    if copy.lent is not None:
        return copy.lent.source
    return f"{copy.font.font_object} {copy.font.name}"


def lacks_a_keyboard_letter(letters: Mapping[str, FontCopy]) -> bool:
    keyboard = (chr(code) for block in KEYBOARD_RANGES for code in block)
    return any(ch not in letters for ch in keyboard)


@dataclass(slots=True)
class _KeptWidths:
    """Finding which letters draw is slow, so a process keeps them, the latest few."""

    by_digest: dict[bytes, dict[str, float]] = field(default_factory=dict, repr=False)

    def widths(self, digest: bytes, make: Callable[[], dict[str, float]]) -> dict[str, float]:
        if digest in self.by_digest:
            return self.by_digest[digest]
        if len(self.by_digest) >= constants.LENT_COPIES_KEPT:
            del self.by_digest[next(iter(self.by_digest))]
        widths = self.by_digest[digest] = make()
        return widths

    def forget(self) -> None:
        self.by_digest.clear()


_kept_widths = _KeptWidths()


def lent_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Kept by a digest of the bytes, not the name: another file can share the name."""
    digest = hashlib.sha256(embedded.file).digest()
    return _kept_widths.widths(digest, partial(_previewed_widths, embedded))


def _previewed_widths(embedded: EmbeddedFont) -> dict[str, float]:
    """Only previewed letters are checked for a shape: a large font holds far more outlines."""
    previewed = coverage_of(embedded.file).previewed_letters()
    return {ch: embedded.program.advance(ch) * EM for ch in previewed}


def why_unlike_listed(copy: FontCopy, listed: Mapping[str, float] | None) -> Message | None:
    if listed is None:
        return Message("font_no_width_list")
    shared = [ch for ch in copy.widths if ch in listed and not ch.isspace()]
    if len(shared) < constants.SAME_FONT_SHARED:
        return Message("copy_too_few_shared")
    widths_differ = any(
        abs(copy.widths[ch] - listed[ch]) > constants.SAME_WIDTH for ch in shared
    )
    if widths_differ:
        return Message("copy_other_widths")
    return None


def why_turned_away(
    other: FontCopy, *, own: FontCopy, letters: Mapping[str, FontCopy]
) -> Message | None:
    same_kind = other.font.kind == own.font.kind
    same_way = (other.embedded.coded is None) == (own.embedded.coded is None)
    if not (same_kind and same_way):
        return Message("copy_stored_apart")
    shared = [ch for ch in other.widths if ch in letters and not ch.isspace()]
    if len(shared) < constants.SAME_FONT_SHARED:
        return Message("copy_too_few_shared")
    widths_differ = any(
        abs(other.widths[ch] - letters[ch].widths[ch]) > constants.SAME_WIDTH for ch in shared
    )
    if widths_differ:
        return Message("copy_other_widths")
    return None
