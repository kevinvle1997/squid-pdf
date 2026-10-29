"""A redaction's life: which spans it covers, then whether their text is really gone.

Checked by re-reading the saved file. Export and the CLI's `redact` both use it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from squidpdf.core import (
    QUARTER_TURNS,
    DriverFactory,
    Engine,
    HiddenPlace,
    QuarterTurn,
    Rect,
    Span,
    any_word_left,
    counted_words,
    open_pdf,
    times_in,
)
from squidpdf.editing.apply import Resolved, new_text_turns
from squidpdf.editing.edits import Insert
from squidpdf.editing.errors import RedactionCovered, RedactionFailed
from squidpdf.editing.fit import insert_span
from squidpdf.editing.types import EditedText, Redaction


@dataclass(frozen=True, slots=True, eq=False)
class RedactionController:
    """One edit list's redactions, from the edits to the saved file. Made by `redactions_of`."""

    redacted: tuple[Span, ...]  # the spans it follows, numbered as in the original
    covered: frozenset[str]  # those new text sharing a word with is drawn over, by id
    # How many times each span's page drew each of its `counted_words` before the edits, by id.
    drawn_before: dict[str, list[int]]

    def hidden_places(self, engine: Engine) -> dict[str, list[HiddenPlace]]:
        """Where else each redacted span's words have a hidden copy, by span id.

        Ask before the edits run: they take the words out.
        """
        return engine.redaction.hidden_places(self.redacted)

    def drawn(
        self,
        engine: Engine,
        *,
        hidden: dict[str, list[HiddenPlace]],
        edited: dict[int, EditedText],
    ) -> dict[str, Redaction]:
        """Each redaction as the document in memory has it, by span id.

        Render's early run of export's check, on pages still numbered as in the original.
        `hidden` is what `hidden_places` said before the edits; `edited`, what they changed.
        """
        redacted = list(self.redacted)
        most = self._most_drawn(edited)
        left = _words_left(engine, redacted, redacted=redacted, most_drawn=most)
        still_there = {span.id for span in left.spans}
        blamed = self._blamed_on_new_text(left)
        return {
            span.id: Redaction(
                span.id not in still_there, hidden[span.id], covered=span.id in blamed
            )
            for span in self.redacted
        }

    def check_saved(
        self,
        path: str,
        *,
        pages: Sequence[int],
        edited: dict[int, EditedText],
        driver_factory: DriverFactory,
    ) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Read in `driver_factory`'s driver; `pages` is the saved order, by original number, and
        `edited` what the edits changed. Raises RedactionFailed naming a span still there, by
        its original page, or RedactionCovered when each one left may be new text's.
        """
        as_saved = list(_as_saved(self.redacted, pages))
        most = self._most_drawn(edited)
        with open_pdf(path, driver_factory=driver_factory) as saved:
            left = _words_left(saved, as_saved, redacted=self.redacted, most_drawn=most)
        # Every redacted span's words are gone.
        if not left.spans:
            return
        blamed = self._blamed_on_new_text(left)
        # One new text can't explain is said first: moving the new text won't save it.
        unexplained = [span for span in left.spans if span.id not in blamed]
        named = unexplained[0] if unexplained else left.spans[0]
        # By its original page: `left` is numbered as saved.
        refused = next(span for span in self.redacted if span.id == named.id)
        if refused.id in blamed:
            raise RedactionCovered(refused.id, refused.page + 1)
        raise RedactionFailed(refused.id, refused.text, refused.page + 1)

    def _most_drawn(self, edited: dict[int, EditedText]) -> dict[str, list[int]]:
        """How many times each span's page may draw each of its words once `edited`, by id.

        As often as before, less each time the edits took one out, and each time they drew one.
        """
        most: dict[str, list[int]] = {}
        for span in self.redacted:
            words = counted_words(span.text)
            on_page = edited[span.page]
            taken = _times_in_all(on_page.erased, words)
            added = _times_in_all(on_page.drawn, words)
            before = self.drawn_before[span.id]
            most[span.id] = [
                max(0, was - out) + more
                for was, out, more in zip(before, taken, added, strict=True)
            ]
        return most

    def _blamed_on_new_text(self, left: _WordsLeft) -> set[str]:
        """The spans left only in their box, where new text sharing a word with them is drawn.

        New text writes no hidden copy, so a word found in one is the redaction's.
        """
        in_hidden_copy = {span.id for span in left.hidden}
        return {
            span.id
            for span in left.in_box
            if span.id in self.covered and span.id not in in_hidden_copy
        }


def redactions_of(
    engine: Engine, resolved: Resolved, redacted: Sequence[Span]
) -> RedactionController:
    """The controller for the `redacted` spans, with the new text over each.

    Call it before the edits run: erasing can drop the fonts new text is measured with.
    """
    return RedactionController(
        tuple(redacted),
        covered=_covered_in(engine, resolved, redacted),
        drawn_before=_drawn_before(engine, redacted),
    )


def _drawn_before(engine: Engine, redacted: Sequence[Span]) -> dict[str, list[int]]:
    """How many times each span's page draws each of its `counted_words` now, by id."""
    drawn = {
        page: engine.redaction.drawn_text(page) for page in {span.page for span in redacted}
    }
    return {span.id: times_in(drawn[span.page], counted_words(span.text)) for span in redacted}


def _times_in_all(texts: Sequence[str], words: Sequence[str]) -> list[int]:
    """How many times `texts`, together, hold each of `words`."""
    counts = [times_in(text, words) for text in texts]
    return [sum(each) for each in zip(*counts, strict=True)] if counts else [0] * len(words)


def _covered_in(engine: Engine, resolved: Resolved, redacted: Sequence[Span]) -> frozenset[str]:
    """Those of the `redacted` spans new text sharing a word with is drawn over, by id.

    The check reads that word in the box too. Pages are numbered as in the original.
    """
    pages_redacted = {span.page for span in redacted}
    on_redacted_pages = [
        listed.insert for listed in resolved.inserts if listed.insert.page in pages_redacted
    ]
    # No new text on a page with a redaction: nothing to measure.
    if not on_redacted_pages:
        return frozenset()
    turns_ccw = new_text_turns(engine, resolved)
    drawn = [
        (insert, _drawn_box(engine, insert, turn_ccw=turns_ccw[insert.page]))
        for insert in on_redacted_pages
    ]
    return frozenset(
        span.id for span in redacted if any(_covers(insert, box, span) for insert, box in drawn)
    )


def _covers(insert: Insert, box: Rect, span: Span) -> bool:
    """Whether new text drawn in `box` is over `span` and shares a word with it."""
    over = insert.page == span.page and box.overlaps(span.bbox)
    return over and any_word_left(span.text, insert.text)


def _drawn_box(engine: Engine, insert: Insert, *, turn_ccw: QuarterTurn) -> Rect:
    """The box new text's letters take: from its origin along its line, and up by its size."""
    span = insert_span(engine, insert)
    width = engine.fit.measure(span, insert.text)
    x, y = insert.origin
    cos, sin = QUARTER_TURNS[turn_ccw]
    # Along the line and up from it, turned; the page's y grows downward.
    along_x, along_y = cos * width, -sin * width
    up_x, up_y = -sin * insert.size, -cos * insert.size
    xs = (x, x + along_x, x + up_x, x + along_x + up_x)
    ys = (y, y + along_y, y + up_y, y + along_y + up_y)
    return Rect(min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True, slots=True)
class _WordsLeft:
    """The spans with words still in the file: in their box, in a hidden copy, or elsewhere."""

    in_box: list[Span]
    hidden: list[Span]
    elsewhere: list[Span]  # drawn more often than the edits leave room for: moved off their box

    @property
    def spans(self) -> list[Span]:
        """Every span with words left, those in their box first; one may be in several."""
        return self.in_box + self.hidden + self.elsewhere


def _words_left(
    engine: Engine,
    placed: list[Span],
    *,
    redacted: Sequence[Span],
    most_drawn: dict[str, list[int]],
) -> _WordsLeft:
    """The spans with words still in the file: in their box, a hidden copy, or drawn elsewhere.

    Render and export share it, so the warning and the refusal can't drift apart. `placed`
    are the spans on the pages they're read on, for boxes and pages' copies; `redacted`,
    pages kept or not, for the document's own. `most_drawn` is `_most_drawn`'s, by id.
    """
    hidden = engine.redaction.still_hidden(placed)
    drawn = {page: engine.redaction.drawn_text(page) for page in {span.page for span in placed}}
    elsewhere = [
        span
        for span in placed
        if _drawn_more(
            times_in(drawn[span.page], counted_words(span.text)), most_drawn[span.id]
        )
    ]
    return _WordsLeft(
        engine.redaction.still_there(placed),
        hidden=hidden + engine.redaction.still_hidden_in_document(redacted),
        elsewhere=elsewhere,
    )


def _drawn_more(times: list[int], most: list[int]) -> bool:
    """Whether any word is drawn more `times` than the `most` it may be."""
    return any(now > allowed for now, allowed in zip(times, most, strict=True))


def _as_saved(redacted: Sequence[Span], pages: Sequence[int]) -> Iterator[Span]:
    """Each redacted span on the page it went to in `pages`; none on a page left out."""
    by_page: dict[int, list[Span]] = {}
    for span in redacted:
        by_page.setdefault(span.page, []).append(span)
    for saved_page, original_page in enumerate(pages):
        # .get: most pages have no redaction on them.
        for span in by_page.get(original_page, []):
            yield replace(span, page=saved_page)
