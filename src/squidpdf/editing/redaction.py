"""A redaction's life: which spans it covers, then whether their text is really gone.

Checked by re-reading the saved file. Export and the CLI's `redact` both use it. A span's
redacted words are checked run by run, each a span of its own, and said of by the span.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

from squidpdf.core import (
    QUARTER_TURNS,
    DrawnElsewhere,
    DrawnGlyph,
    DriverFactory,
    Engine,
    HiddenPlace,
    QuarterTurn,
    Rect,
    RedactedRun,
    RestOfLine,
    Span,
    any_word_left,
    open_pdf,
)
from squidpdf.editing.apply import Resolved, new_text_turns
from squidpdf.editing.edits import Insert
from squidpdf.editing.errors import (
    RedactionCovered,
    RedactionFailed,
    RedactionMoved,
    RedactionUnchecked,
)
from squidpdf.editing.fit import insert_span
from squidpdf.editing.redacted_words import runs_of, runs_text
from squidpdf.editing.types import EditedPage, Redacted, Redaction

# A run of redacted words, by its span's id and its words: a span's runs differ by their words.
type _RunKey = tuple[str, str]


@dataclass(frozen=True, slots=True, eq=False)
class RedactionController:
    """One edit list's redactions, from the edits to the saved file. Made by `redactions_of`."""

    redacted: tuple[Redacted, ...]  # what each span loses, numbered as in the original
    covered: frozenset[str]  # those new text sharing a word with is drawn over, by id
    edited: dict[int, EditedPage]  # what the edits erase and draw, by original page
    # The glyphs each page with a redaction still draws once erased, by original page.
    kept: dict[int, list[DrawnGlyph]]

    def hidden_places(self, engine: Engine) -> dict[str, list[HiddenPlace]]:
        """Where else each redacted span's words have a hidden copy, by span id.

        Ask before the edits run: they take the words out.
        """
        return engine.redaction.hidden_places(runs_of(self.redacted))

    def drawn(
        self, engine: Engine, *, hidden: dict[str, list[HiddenPlace]]
    ) -> dict[str, Redaction]:
        """Each redaction as the document in memory has it, by span id.

        Render's early run of export's check, on pages still numbered as in the original.
        `hidden` is what `hidden_places` said before the edits.
        """
        runs = runs_of(self.redacted)
        pages = {run.page: run.page for run in runs}
        left = _words_left(engine, runs, redacted=runs, checked=self, pages=pages)
        still_there = {span.id for span in left.spans}
        moved = {span.id for span in left.moved}
        unchecked = {span.id for span in left.unchecked}
        blamed = self._blamed_on_new_text(left)
        return {
            each.span.id: Redaction(
                each.span.id not in still_there | moved | unchecked,
                hidden[each.span.id],
                covered=each.span.id in blamed,
                moved=each.span.id in moved - still_there,
                unchecked=each.span.id in unchecked - still_there - moved,
                rest=left.rests[each.span.id],
                words=runs_text(each),
                page=each.span.page,
                runs=[
                    (asked, run.bbox) for asked, run in zip(each.ranges, each.runs, strict=True)
                ],
            )
            for each in self.redacted
        }

    def check_saved(
        self, path: str, *, pages: Sequence[int], driver_factory: DriverFactory
    ) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Read in `driver_factory`'s driver, which drew the edits; `pages` is the saved order.
        Raises RedactionFailed naming a span still there, by its original page, or
        RedactionCovered when each is new text's; else RedactionMoved, or RedactionUnchecked.
        """
        runs = runs_of(self.redacted)
        as_saved = list(_as_saved(runs, pages))
        originals = dict(enumerate(pages))
        with open_pdf(path, driver_factory=driver_factory) as saved:
            left = _words_left(saved, as_saved, redacted=runs, checked=self, pages=originals)
        # Every redacted span's words are gone, but other text moved: refused, as it is.
        if not left.spans and left.moved:
            moved = self._redacted(left.moved[0].id)
            raise RedactionMoved(runs_text(moved), moved.span.page + 1)
        # Every redacted span's words are gone, but a rest's landing can't be read: refused.
        if not left.spans and left.unchecked:
            unchecked = self._redacted(left.unchecked[0].id)
            raise RedactionUnchecked(runs_text(unchecked), unchecked.span.page + 1)
        # Every redacted span's words are gone, and nothing else moved.
        if not left.spans:
            return
        blamed = self._blamed_on_new_text(left)
        # One new text can't explain is said first: moving the new text won't save it.
        unexplained = [span for span in left.spans if span.id not in blamed]
        named = unexplained[0] if unexplained else left.spans[0]
        # By its original page: `left` is numbered as saved. A run by its own words.
        refused = next(run for run in runs if _key(run) == _key(named))
        if refused.id in blamed:
            raise RedactionCovered(refused.id, refused.page + 1)
        # Its words as render quotes them: every run of them.
        raise RedactionFailed(
            refused.id, runs_text(self._redacted(refused.id)), refused.page + 1
        )

    def _redacted(self, span_id: str) -> Redacted:
        """What the redactions of the span `span_id` take."""
        return next(each for each in self.redacted if each.span.id == span_id)

    def drawn_elsewhere(
        self, engine: Engine, *, page: int, original: int
    ) -> tuple[DrawnElsewhere, dict[str, RestOfLine]]:
        """What `page` draws where it shouldn't, and the rest of each span's line, by span id.

        `original` is its number before any was renumbered. A glyph the erase meant to leave,
        where it isn't, or one it meant to take, anywhere: neither where the page drew it, nor
        on a line an edit drew.
        """
        on_page = [each for each in self.redacted if each.span.page == original]
        elsewhere = engine.redaction.drawn_elsewhere(
            page,
            self.kept[original],
            lines=self.edited[original].drawn,
            rests=[each.span for each in on_page],
            words=[run.text for each in on_page for run in each.runs],
        )
        # A rest of None: its words weren't among its letters, so the whole line went.
        rests: dict[str, RestOfLine] = {
            each.span.id: "removed" if each.rest is None else rest
            for each, rest in zip(on_page, elsewhere.rests, strict=True)
        }
        return elsewhere, rests

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
    engine: Engine,
    resolved: Resolved,
    redacted: Sequence[Redacted],
    *,
    edited: dict[int, EditedPage],
) -> RedactionController:
    """The controller for what each span loses, `redacted`, with the new text over each.

    `edited` is what the edits erase and draw. Call it before the edits run: erasing can drop
    the fonts new text is measured with, and it reads what each page draws now.
    """
    runs = runs_of(redacted)
    return RedactionController(
        tuple(redacted),
        covered=_covered_in(engine, resolved, runs),
        edited=edited,
        kept={
            page: engine.redaction.kept_by(page, edited[page].erased)
            for page in {run.page for run in runs}
        },
    )


def _key(run: Span) -> _RunKey:
    """A run of redacted words, told from its span's other runs by its words."""
    return (run.id, run.text)


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
    """The runs with words still in the file, and those on a page whose erase moved text.

    Words still in the file are in their box, in a hidden copy, or drawn elsewhere.
    """

    in_box: list[Span]
    hidden: list[RedactedRun]
    elsewhere: list[RedactedRun]  # on a page that draws a glyph the erase was to take, wherever
    moved: list[RedactedRun]  # on a page that draws a glyph it was to leave somewhere else
    unchecked: list[RedactedRun]  # on a page where a rest set along can't be checked
    rests: dict[str, RestOfLine]  # what became of the rest of each span's line, by its id

    @property
    def spans(self) -> list[Span]:
        """Every run with words left, those in their box first; one may be in several."""
        return [*self.in_box, *self.hidden, *self.elsewhere]


def _words_left(
    engine: Engine,
    placed: list[RedactedRun],
    *,
    redacted: Sequence[RedactedRun],
    checked: RedactionController,
    pages: Mapping[int, int],
) -> _WordsLeft:
    """The runs with words still in the file: in their box, a hidden copy, or drawn elsewhere.

    Render and export share it, so the warning and the refusal can't drift apart. `placed`
    are the runs on the pages they're read on, for boxes and pages' copies; `redacted`,
    pages kept or not, for the document's own. `pages` gives each page read its original.
    """
    hidden = engine.redaction.still_hidden(placed)
    checked_pages = {
        page: checked.drawn_elsewhere(engine, page=page, original=pages[page])
        for page in {run.page for run in placed}
    }
    drawn_elsewhere = {page: elsewhere for page, (elsewhere, _rests) in checked_pages.items()}
    return _WordsLeft(
        engine.redaction.still_there(placed),
        hidden=hidden + engine.redaction.still_hidden_in_document(redacted),
        elsewhere=[run for run in placed if drawn_elsewhere[run.page].taken],
        moved=[run for run in placed if drawn_elsewhere[run.page].moved],
        unchecked=[run for run in placed if drawn_elsewhere[run.page].unchecked],
        rests={
            span_id: rest
            for _drawn, rests in checked_pages.values()
            for span_id, rest in rests.items()
        },
    )


def _as_saved(runs: Sequence[RedactedRun], pages: Sequence[int]) -> Iterator[RedactedRun]:
    """Each run on the page it went to in `pages`; none on a page left out."""
    by_page: dict[int, list[RedactedRun]] = {}
    for run in runs:
        by_page.setdefault(run.page, []).append(run)
    for saved_page, original_page in enumerate(pages):
        # .get: most pages have no redaction on them.
        for run in by_page.get(original_page, []):
            yield replace(run, page=saved_page)
