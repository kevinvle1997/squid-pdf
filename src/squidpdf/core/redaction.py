"""Whether a redaction's words are gone: from each span's box, and from every hidden copy.

Read-only checks, written against the driver's primitives, asked as
`engine.redaction` before an edit (where else the words hide) and after it
(whether any are left). The engine's erase matches words as these checks do.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import partial

from squidpdf.core.constants import MOVED_PT, SIZE_EPS
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.types import (
    COPY_PLACES,
    CopyPlace,
    HiddenCopy,
    HiddenPlace,
    PlacedLetter,
    RestOfLine,
    Span,
    asked_by_page,
    by_page,
)

# A word: a run of letters and digits (`\w` without its underscore).
_WORD = re.compile(r"[^\W_]+")


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
        holding = partial(holds, whole_words(asked))
        document_copies = self.driver.document_hidden_copies(holding)
        places: dict[str, list[CopyPlace]] = {}
        for page, on_page in by_page(asked).items():
            places |= self._places_on(page, on_page, document_copies=document_copies)
        signed: list[HiddenPlace] = ["signatures"] if self.driver.is_signed() else []
        return {span_id: [*held, *signed] for span_id, held in places.items()}

    def _places_on(
        self, page: int, spans: list[Span], *, document_copies: list[HiddenCopy]
    ) -> dict[str, list[CopyPlace]]:
        """Where each of `spans`, all on `page`, has a hidden copy: there, or the document's.

        By span id: the runs of words a redaction takes of one span are that span's words.
        """
        on_page = [
            HiddenCopy("screen_reader_text", text) for text in self.driver.hidden_copies(page)
        ]
        copies = on_page + document_copies
        runs_by_id: dict[str, list[Span]] = {}
        for span in spans:
            runs_by_id.setdefault(span.id, []).append(span)
        return {
            span_id: _places_holding(whole_words(runs), copies)
            for span_id, runs in runs_by_id.items()
        }

    def letters_of(self, span: Span) -> list[PlacedLetter]:
        """The letters the page draws for `span`: in its box, at its size, in reading order.

        Other text drawn in its box at another size isn't its own.
        """
        [letters] = self.driver.letters_in(span.page, [span.bbox])
        return [letter for letter in letters if abs(letter.size - span.size) <= SIZE_EPS]

    def rest_of_line(self, span: Span, rest: Sequence[PlacedLetter]) -> RestOfLine:
        """Where the letters a redaction left of `span`, `rest`, are now: in place, or not.

        Removed when fewer are drawn in its box than it left; else moved.
        """
        now = self.letters_of(span)
        out_of_place = [was for was in rest if not any(_same_place(was, is_) for is_ in now)]
        if not out_of_place:
            return "in_place"
        drawn = [letter for letter in now if not letter.letter.isspace()]
        return "removed" if len(drawn) < len(rest) else "moved"

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with any word of their text still in their box. A black box won't hide it.

        Part of a card number is as much a leak as all of it. Only each span's
        own box is read, so the same words elsewhere aren't a leak. Spaces are
        ignored, so respacing can't hide a leftover.
        """
        return asked_by_page(spans, self._left_on)

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with any word of their text still in their box."""
        texts = self.driver.text_in(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def drawn_text(self, page: int) -> str:
        """Every letter the page's drawing writes, in the order drawn, on the page or off it.

        Wherever a letter lands, it's read: an erase that moves a line can't hide its words.
        """
        return "".join(glyph.letter for glyph in self.driver.drawn_glyphs(page))

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
            partial(holds, whole_words(checked))
        )
        return [
            span for span in checked if _places_holding(whole_words([span]), document_copies)
        ]


def _same_place(was: PlacedLetter, now: PlacedLetter) -> bool:
    """Whether `now` is the letter `was`, where it was, give or take a redraw's rounding."""
    return now.letter == was.letter and math.dist(was.origin, now.origin) <= MOVED_PT


def words_around(text: str, asked: range) -> range | None:
    """`asked` of `text` widened to whole words, less spaces at its ends; None if all spaces.

    What a redaction of some words of a span takes. The browser widens a selection the same way:
    `tests/editing/redacted_words_cases.json` holds both to it.
    """
    start, end = asked.start, asked.stop
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    # Nothing but spaces: no word to take.
    if start == end:
        return None
    while start > 0 and is_word_letter(text[start - 1]) and is_word_letter(text[start]):
        start -= 1
    while end < len(text) and is_word_letter(text[end - 1]) and is_word_letter(text[end]):
        end += 1
    return range(start, end)


def is_word_letter(letter: str) -> bool:
    """Whether `letter` is part of a word: a letter, a digit or a mark on one (L, N or M)."""
    return unicodedata.category(letter)[0] in "LNM"


def any_word_left(text: str, left: str) -> bool:
    """Whether any word of `text`, or all of it, is in `left`, spaces aside.

    A one-letter word counts only within the whole text: redaction labels often hold "A".
    So a span of one letter is still looked for, as its whole text.
    """
    leftover = "".join(left.split())
    words = [word for word in text.split() if len(word) > 1]
    return any(word in leftover for word in [*words, "".join(text.split())])


def counted_words(text: str) -> list[str]:
    """The words of `text` a count looks for, as `any_word_left` does, spaces aside, NFKC."""
    words = [word for word in text.split() if len(word) > 1]
    return [_spaceless(word) for word in [*words, text]]


def times_in(text: str, words: Sequence[str]) -> list[int]:
    """How many times `text` holds each of `counted_words`' words, spaces aside."""
    spaceless = _spaceless(text)
    return [spaceless.count(word) for word in words]


def _spaceless(text: str) -> str:
    """`text` with no spaces, each ligature as its letters: two spellings of a line compare."""
    return "".join(_comparable(text).split())


def whole_words(spans: Iterable[Span]) -> re.Pattern[str]:
    """The spans' words as one pattern: whole words only, in any case, the longest first.

    In any case, since a hidden copy is typed, not the page's own letters. Longest first, so
    a whole text goes whole, its one-letter words with it. Redacted words of a line match
    joined to its other words too, as a hidden copy may write them ("Acct1234").
    """
    asked = list(spans)
    choices = {choice for span in asked for choice in _word_choices(span.text)}
    longest_first = sorted(choices, key=len, reverse=True)
    beside = sorted({re.escape(word) for span in asked for word in _line_words(span)})
    # Whole: no letter or digit just before it, or just after, but a word of its line.
    before = "|".join([r"(?<![^\W_])", *(f"(?<={word})" for word in beside)])
    after = "|".join([r"(?![^\W_])", *(f"(?={word})" for word in beside)])
    return re.compile(rf"(?:{before})(?:{'|'.join(longest_first)})(?:{after})", re.IGNORECASE)


def _line_words(span: Span) -> list[str]:
    """The words of the line `span` holds redacted words of; none for a span that's all of it.

    Redacted words keep their line's pieces (`fragments`), so the line can be read.
    """
    line = "".join(fragment.text for fragment in span.fragments)
    # All of its line, or new text, which has no pieces.
    if line in ("", span.text):
        return []
    return _WORD.findall(_comparable(line))


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


def holds(words: re.Pattern[str], hidden_copy: str) -> bool:
    """Whether a hidden copy, or a link's action, holds any of `words`, ligatures as letters."""
    return words.search(_comparable(hidden_copy)) is not None


def _any_holds(words: re.Pattern[str], hidden_copies: list[str]) -> bool:
    """Whether any of `hidden_copies` holds one of `words`."""
    return any(holds(words, hidden_copy) for hidden_copy in hidden_copies)


def _places_holding(words: re.Pattern[str], copies: list[HiddenCopy]) -> list[CopyPlace]:
    """The places of the copies holding any of `words`, each once, in their named order."""
    held = {hidden_copy.place for hidden_copy in copies if holds(words, hidden_copy.text)}
    return [place for place in COPY_PLACES if place in held]


def without(words: re.Pattern[str], hidden_copy: str) -> str:
    """A hidden copy with `words` deleted and the spaces closed up; as it was if none are in it.

    What's left has its ligatures spelled out, as it was compared.
    """
    if not holds(words, hidden_copy):
        return hidden_copy
    return " ".join(words.sub("", _comparable(hidden_copy)).split())
