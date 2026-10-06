"""Whether a redaction's words are gone: from each span's box, and from every hidden copy.

Read-only checks, written against the driver's primitives, asked as
`engine.redaction` before an edit (where else the words hide) and after it
(whether any are left). The engine's erase matches words as these checks do.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial

from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.types import COPY_PLACES, CopyPlace, HiddenCopy, HiddenPlace, Span, by_page

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
        left = (self._left_on(page, on_page) for page, on_page in by_page(spans).items())
        return [span for on_page in left for span in on_page]

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with any word of their text still in their box."""
        texts = self.driver.text_in(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def still_hidden(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with a word of their text still in a hidden copy on their page.

        Matched as `Engine.drop_hidden_copies` deletes them (`whole_words`).
        """
        left = (self._hidden_on(page, on_page) for page, on_page in by_page(spans).items())
        return [span for on_page in left for span in on_page]

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
