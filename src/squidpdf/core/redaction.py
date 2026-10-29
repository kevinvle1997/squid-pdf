"""Whether a redaction's words are gone: from each span's box, and from every hidden copy.

Read-only checks, written against the driver's primitives, asked as
`engine.redaction` before an edit (where else the words hide) and after it
(whether any are left). The engine's erase matches words as these checks do.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial

from squidpdf.core.constants import MOVED_PT, ROUNDED_PT
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.types import (
    COPY_PLACES,
    QUARTER_TURNS,
    CopyPlace,
    DrawnElsewhere,
    DrawnGlyph,
    DrawnLine,
    HiddenCopy,
    HiddenPlace,
    PlacedLetter,
    Rect,
    Span,
    asked_by_page,
    by_page,
)

# A word: a run of letters and digits (`\w` without its underscore).
_WORD = re.compile(r"[^\W_]+")
# A box around everything a page can draw: to read all of its letters.
_EVERYWHERE = Rect(-math.inf, -math.inf, math.inf, math.inf)


@dataclass(frozen=True, slots=True, eq=False)
class RedactionCheck:
    """Where a redacted span's words hide, and whether any are left. Made by `open_engine`."""

    driver: PdfDriver

    def hidden_places(self, spans: Iterable[Span]) -> dict[str, list[HiddenPlace]]:
        """Where else each span's words have a hidden copy, by span id.

        Ask before `Engine.drop_hidden_copies` takes them out. A signed document names its
        signatures for every span: any redaction takes them.
        """
        asked = list(spans)
        # No span: the document's own copies needn't be read.
        if not asked:
            return {}
        holding = partial(_holds, whole_words(asked))
        document_copies = self.driver.document_hidden_copies(holding)
        places: dict[str, list[CopyPlace]] = {}
        for page, on_page in by_page(asked).items():
            places |= self._places_on(page, on_page, document_copies=document_copies)
        signed: list[HiddenPlace] = ["signatures"] if self.driver.is_signed() else []
        return {span_id: [*held, *signed] for span_id, held in places.items()}

    def _places_on(
        self, page: int, spans: list[Span], *, document_copies: list[HiddenCopy]
    ) -> dict[str, list[CopyPlace]]:
        """Where each of `spans`, all on `page`, has a hidden copy: there, or the document's."""
        on_page = [
            HiddenCopy("screen_reader_text", text) for text in self.driver.hidden_copies(page)
        ]
        copies = on_page + document_copies
        return {span.id: _places_holding(whole_words([span]), copies) for span in spans}

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with any word of their text still in their box. A black box won't hide it.

        Part of a card number is as much a leak as all of it. Only each span's
        own box is read, so the same words elsewhere aren't a leak. Spaces are
        ignored, so respacing can't hide a leftover.
        """
        return asked_by_page(spans, self._left_on)

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with any word of their text still in their box."""
        texts = self.driver.text_in(page, [span.bbox.grown(ROUNDED_PT) for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def kept_by(self, page: int, erased: Sequence[Rect]) -> list[DrawnGlyph]:
        """The glyphs the page draws that an erase of `erased` boxes leaves, spaces aside.

        Ask before the erase. It takes the glyph of each letter whose middle is in a box.
        """
        reached = [box.grown(ROUNDED_PT) for box in erased]
        taken = [letter for box in self.driver.letters_in(page, reached) for letter in box]
        return _less(_letters_of(self.driver.drawn_glyphs(page)), taken, near=_same_place)

    def drawn_elsewhere(
        self,
        page: int,
        kept: Sequence[DrawnGlyph],
        *,
        lines: Sequence[DrawnLine],
        words: Sequence[str] = (),
    ) -> DrawnElsewhere:
        """The glyphs drawn now, spaces aside, the erase was to take, or moved.

        Every glyph is read, on or off the page. None is one `kept` where it was, or of `lines`
        the edits drew. A run spelling a word of `words` whole, as the page reads, is those
        words; else, some of a line drawn before, in order, is that text moved.
        """
        drawn = self.driver.drawn_glyphs(page)
        now = _letters_of(drawn)
        gone = _less(list(kept), now, near=_same_place)
        new = _less(now, kept, near=_same_place)
        gone_lines = _runs(gone, among=kept)
        runs = _runs(_not_on(new, lines), among=now)
        # Spelling words the erase takes, a run is those words, whatever other letters went.
        # The page's reading, for where it puts spaces: read only if a glyph is out of place.
        reading = self.driver.letters_in(page, [_EVERYWHERE])[0] if runs else []
        off = [run for run in runs if not _spells_any(_spelled(run, reading), words)]
        moved = [run for run in off if _part_of(run, gone_lines) is not None]
        taken = [run for run in runs if not any(run is each for each in moved)]
        return DrawnElsewhere(
            [glyph for run in taken for glyph in run], [glyph for run in moved for glyph in run]
        )

    def still_hidden(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with a word of their text still in a hidden copy on their page.

        Matched as `Engine.drop_hidden_copies` deletes them (`whole_words`).
        """
        return asked_by_page(spans, self._hidden_on)

    def _hidden_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with a word of their text in a hidden copy there."""
        hidden_copies = self.driver.hidden_copies(page)
        return [span for span in spans if _any_holds(whole_words([span]), hidden_copies)]

    def still_hidden_in_document(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with a word of their text still in one of the document's own hidden copies.

        Read as `Engine.drop_hidden_copies` deletes them, whichever pages were kept:
        none sits on one.
        """
        checked = list(spans)
        # No span, as an export with no redaction: the document's own needn't be read.
        if not checked:
            return []
        document_copies = self.driver.document_hidden_copies(
            partial(_holds, whole_words(checked))
        )
        return [
            span for span in checked if _places_holding(whole_words([span]), document_copies)
        ]


def any_word_left(text: str, left: str) -> bool:
    """Whether any word of `text`, or all of it, is in `left`, spaces aside.

    A one-letter word counts only within the whole text: redaction labels often hold "A".
    So a span of one letter is still looked for, as its whole text.
    """
    leftover = "".join(left.split())
    words = [word for word in text.split() if len(word) > 1]
    return any(word in leftover for word in [*words, "".join(text.split())])


def _spelled(run: list[DrawnGlyph], reading: list[PlacedLetter]) -> str:
    """`run` as the page's `reading` spells it, from its first glyph to its last.

    With a space wherever the reading puts one: at a gap kerned wide enough, as at a space
    drawn, since that's what the reader sees. A run the reading doesn't hold, off the page,
    is its letters alone.
    """
    first = _read_at(run[0], reading, start=0)
    last = None if first is None else _read_at(run[-1], reading, start=first)
    if first is None or last is None:
        return "".join(glyph.letter for glyph in run)
    return "".join(letter.letter for letter in reading[first : last + 1])


def _read_at(glyph: DrawnGlyph, reading: list[PlacedLetter], *, start: int) -> int | None:
    """Where in `reading`, from `start` on, `glyph` is read: its letter, where it's drawn."""
    return next(
        (
            at
            for at in range(start, len(reading))
            if reading[at].letter == glyph.letter and _same_place(reading[at], glyph)
        ),
        None,
    )


def _spells_any(spelled: str, words: Sequence[str]) -> bool:
    """Whether `spelled` holds a word of any of `words`, or all of one, whole.

    Whole: "Ann" isn't in "Annual", nor "1234" in "12 34". A one-letter word counts only
    within the whole text, as `any_word_left` reads one.
    """
    line = " ".join(spelled.split())
    looked_for = [
        word
        for text in words
        for word in [*(word for word in text.split() if len(word) > 1), " ".join(text.split())]
    ]
    return any(re.search(_whole_word(word), line) for word in looked_for)


def _whole_word(word: str) -> str:
    """`word` as a pattern: no letter or digit just before or after it."""
    return rf"(?<![^\W_]){re.escape(word)}(?![^\W_])"


def _not_on(glyphs: list[DrawnGlyph], lines: Sequence[DrawnLine]) -> list[DrawnGlyph]:
    """Of `glyphs`, those none of `lines` has: each of its letters once, on its baseline."""
    spare = [Counter("".join(line.text.split())) for line in lines]
    left: list[DrawnGlyph] = []
    for glyph in glyphs:
        drawing = [
            at
            for at, line in enumerate(lines)
            if spare[at][glyph.letter] > 0 and _on_line(glyph, line)
        ]
        if not drawing:
            left.append(glyph)
            continue
        spare[drawing[0]][glyph.letter] -= 1
    return left


def _on_line(glyph: DrawnGlyph, line: DrawnLine) -> bool:
    """Whether `glyph` starts on `line`'s baseline, from its start on, as the line runs."""
    cos, sin = QUARTER_TURNS[line.turn_ccw]
    # The page's y grows downward.
    direction = (cos, -sin)
    offset = (glyph.origin[0] - line.origin[0], glyph.origin[1] - line.origin[1])
    along = offset[0] * direction[0] + offset[1] * direction[1]
    return abs(_across(direction, offset)) <= MOVED_PT and along >= -MOVED_PT


def _letters_of(glyphs: list[DrawnGlyph]) -> list[DrawnGlyph]:
    """Every one of `glyphs` but a space."""
    return [glyph for glyph in glyphs if not glyph.letter.isspace()]


def _less[Other: (DrawnGlyph, PlacedLetter)](
    glyphs: list[DrawnGlyph],
    others: Sequence[Other],
    *,
    near: Callable[[Other, DrawnGlyph], bool],
) -> list[DrawnGlyph]:
    """`glyphs` less one for each of `others`: of its letter, `near` where it is."""
    spares: dict[str, list[Other]] = {}
    for other in others:
        spares.setdefault(other.letter, []).append(other)
    left: list[DrawnGlyph] = []
    for glyph in glyphs:
        # .get: a letter none of `others` has.
        spare = spares.get(glyph.letter, [])
        found = [at for at, other in enumerate(spare) if near(other, glyph)]
        if not found:
            left.append(glyph)
            continue
        spare.pop(found[0])
    return left


def _same_place(was: DrawnGlyph | PlacedLetter, now: DrawnGlyph) -> bool:
    """Whether `now` starts where `was` did, give or take a rewrite."""
    return math.dist(was.origin, now.origin) <= MOVED_PT


def _runs(glyphs: list[DrawnGlyph], *, among: Sequence[DrawnGlyph]) -> list[list[DrawnGlyph]]:
    """`glyphs`, some of `among` in its order, split into the runs drawn together on a line.

    Split where `among` has another glyph between, or where the line changes.
    """
    runs: list[list[DrawnGlyph]] = []
    at = -1
    for glyph in glyphs:
        before = at
        at = next(place for place in range(at + 1, len(among)) if among[place] is glyph)
        joins = bool(runs) and before == at - 1 and _on_line_of(runs[-1][-1], glyph)
        if joins:
            runs[-1].append(glyph)
            continue
        runs.append([glyph])
    return runs


def _on_line_of(glyph: DrawnGlyph, other: DrawnGlyph) -> bool:
    """Whether `other` starts on `glyph`'s line, read the same way."""
    offset = (other.origin[0] - glyph.origin[0], other.origin[1] - glyph.origin[1])
    same_way = math.dist(glyph.direction, other.direction) <= _SAME_WAY
    return same_way and abs(_across(glyph.direction, offset)) <= MOVED_PT


# Two lines whose directions differ less than this read the same way.
_SAME_WAY = 0.01


def _part_of(run: list[DrawnGlyph], lines: list[list[DrawnGlyph]]) -> list[DrawnGlyph] | None:
    """The glyphs of one of `lines` that `run` is, in their order, wherever each was set.

    None if it's none. They're taken from their line, for no other run to be.
    """
    for line in lines:
        for start, first in enumerate(line):
            if first.letter != run[0].letter:
                continue
            matched = _matched_after(run, line, start=start)
            if matched is not None:
                was = [line[at] for at in matched]
                for at in reversed(matched):
                    del line[at]
                return was
    return None


def _matched_after(
    run: list[DrawnGlyph], line: list[DrawnGlyph], *, start: int
) -> list[int] | None:
    """Where in `line`, from `start` on and in order, each of `run`'s letters was."""
    matched: list[int] = []
    at = start
    for glyph in run:
        found = next(
            (place for place in range(at, len(line)) if line[place].letter == glyph.letter),
            None,
        )
        if found is None:
            return None
        matched.append(found)
        at = found + 1
    return matched


def _across(direction: tuple[float, float], offset: tuple[float, float]) -> float:
    """How far `offset` goes across a line that reads along `direction`."""
    return offset[1] * direction[0] - offset[0] * direction[1]


def whole_words(spans: Iterable[Span]) -> re.Pattern[str]:
    """The spans' words as one pattern: whole words only, in any case, the longest first.

    In any case, since a hidden copy is typed, not the page's own letters. Longest
    first, so a whole text goes whole, its one-letter words with it.
    """
    choices = {choice for span in spans for choice in _word_choices(span.text)}
    longest_first = sorted(choices, key=len, reverse=True)
    # Whole: no letter or digit just before it, or just after.
    return re.compile(rf"(?<![^\W_])(?:{'|'.join(longest_first)})(?![^\W_])", re.IGNORECASE)


def _word_choices(text: str) -> list[str]:
    """`text` whole, then each word of two letters or more, as patterns.

    Alone, "a" or "I" is in most sentences. The whole text matches its words with anything
    but letters and digits between, since a hidden copy may join them otherwise.
    """
    comparable = _comparable(text)
    words = _WORD.findall(comparable)
    whole = r"[\W_]*".join(map(re.escape, words or comparable.split()))
    return [whole, *(re.escape(word) for word in words if len(word) > 1)]


def _comparable(text: str) -> str:
    """`text` with each ligature or styled letter as its plain letters (NFKC).

    A page's letters keep their ligatures, and a typed hidden copy doesn't.
    """
    return unicodedata.normalize("NFKC", text)


def _holds(words: re.Pattern[str], hidden_copy: str) -> bool:
    """Whether a hidden copy holds any of `words`, its ligatures read as their letters."""
    return words.search(_comparable(hidden_copy)) is not None


def _any_holds(words: re.Pattern[str], hidden_copies: list[str]) -> bool:
    """Whether any of `hidden_copies` holds one of `words`."""
    return any(_holds(words, hidden_copy) for hidden_copy in hidden_copies)


def _places_holding(words: re.Pattern[str], copies: list[HiddenCopy]) -> list[CopyPlace]:
    """The places of the copies holding any of `words`, each once, in their named order."""
    held = {hidden_copy.place for hidden_copy in copies if _holds(words, hidden_copy.text)}
    return [place for place in COPY_PLACES if place in held]


def without(words: re.Pattern[str], hidden_copy: str) -> str:
    """A hidden copy with `words` deleted and the spaces closed up; as it was if none are in it.

    What's left has its ligatures spelled out, as it was compared.
    """
    if not _holds(words, hidden_copy):
        return hidden_copy
    return " ".join(words.sub("", _comparable(hidden_copy)).split())
