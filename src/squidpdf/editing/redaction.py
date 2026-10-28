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
    open_pdf,
)
from squidpdf.editing.apply import Resolved, new_text_turns
from squidpdf.editing.edits import Insert
from squidpdf.editing.errors import RedactionCovered, RedactionFailed
from squidpdf.editing.fit import insert_span
from squidpdf.editing.types import Redaction


@dataclass(frozen=True, slots=True, eq=False)
class RedactionController:
    """One edit list's redactions, from the edits to the saved file. Made by `redactions_of`."""

    redacted: tuple[Span, ...]  # the spans it follows, numbered as in the original
    covered: frozenset[str]  # those new text sharing a word with is drawn over, by id

    def hidden_places(self, engine: Engine) -> dict[str, list[HiddenPlace]]:
        """Where else each redacted span's words have a hidden copy, by span id.

        Ask before the edits run: they take the words out.
        """
        return engine.redaction.hidden_places(self.redacted)

    def drawn(
        self, engine: Engine, *, hidden: dict[str, list[HiddenPlace]]
    ) -> dict[str, Redaction]:
        """Each redaction as the document in memory has it, by span id.

        Render's early run of export's check, on pages still numbered as in the original.
        `hidden` is what `hidden_places` said before the edits.
        """
        redacted = list(self.redacted)
        left = _words_left(engine, redacted, redacted=redacted)
        still_there = {span.id for span in left.spans}
        blamed = self._blamed_on_new_text(left)
        return {
            span.id: Redaction(
                span.id not in still_there, hidden[span.id], covered=span.id in blamed
            )
            for span in self.redacted
        }

    def check_saved(
        self, path: str, *, pages: Sequence[int], driver_factory: DriverFactory
    ) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Read in `driver_factory`'s driver, which drew the edits. `pages` is the saved file's
        page order, by original number. Raises RedactionFailed naming a span still there, by
        its original page, or RedactionCovered when each one left may be new text's.
        """
        as_saved = list(_as_saved(self.redacted, pages))
        with open_pdf(path, driver_factory=driver_factory) as saved:
            left = _words_left(saved, as_saved, redacted=self.redacted)
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
    return RedactionController(tuple(redacted), covered=_covered_in(engine, resolved, redacted))


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
    """The spans with words still in the file: in their box, and in a hidden copy."""

    in_box: list[Span]
    hidden: list[Span]

    @property
    def spans(self) -> list[Span]:
        """Every span with words left, those in their box first; one may be in both."""
        return self.in_box + self.hidden


def _words_left(engine: Engine, placed: list[Span], *, redacted: Sequence[Span]) -> _WordsLeft:
    """The spans with words still in the file: in their box, or a hidden copy anywhere.

    Render and export share it, so the warning and the refusal can't drift apart. `placed`
    are the spans on the pages they're read on, for boxes and pages' copies; `redacted`,
    pages kept or not, for the document's own.
    """
    hidden = engine.redaction.still_hidden(placed)
    return _WordsLeft(
        engine.redaction.still_there(placed),
        hidden=hidden + engine.redaction.still_hidden_in_document(redacted),
    )


def _as_saved(redacted: Sequence[Span], pages: Sequence[int]) -> Iterator[Span]:
    """Each redacted span on the page it went to in `pages`; none on a page left out."""
    by_page: dict[int, list[Span]] = {}
    for span in redacted:
        by_page.setdefault(span.page, []).append(span)
    for saved_page, original_page in enumerate(pages):
        # .get: most pages have no redaction on them.
        for span in by_page.get(original_page, []):
            yield replace(span, page=saved_page)
