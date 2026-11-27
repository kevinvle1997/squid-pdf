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

type _RunKey = tuple[str, str]


@dataclass(frozen=True, slots=True, eq=False)
class RedactionController:
    redacted: tuple[Redacted, ...]
    covered: frozenset[str]
    edited: dict[int, EditedPage]
    kept: dict[int, list[DrawnGlyph]]
    hidden: dict[str, list[HiddenPlace]]

    def drawn(self, engine: Engine) -> dict[str, Redaction]:
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
                self.hidden[each.span.id],
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
        runs = runs_of(self.redacted)
        as_saved = list(_as_saved(runs, pages))
        originals = dict(enumerate(pages))
        with open_pdf(path, driver_factory=driver_factory) as saved:
            left = _words_left(saved, as_saved, redacted=runs, checked=self, pages=originals)
        if not left.spans and left.moved:
            moved = self._redacted(left.moved[0].id)
            raise RedactionMoved(runs_text(moved), moved.span.page + 1)
        if not left.spans and left.unchecked:
            unchecked = self._redacted(left.unchecked[0].id)
            raise RedactionUnchecked(runs_text(unchecked), unchecked.span.page + 1)
        if not left.spans:
            return
        blamed = self._blamed_on_new_text(left)
        # One new text can't explain is said first: moving the new text won't save it.
        unexplained = [span for span in left.spans if span.id not in blamed]
        named = unexplained[0] if unexplained else left.spans[0]
        refused = next(run for run in runs if _key(run) == _key(named))
        if refused.id in blamed:
            raise RedactionCovered(refused.id, refused.page + 1)
        raise RedactionFailed(
            refused.id, runs_text(self._redacted(refused.id)), refused.page + 1
        )

    def _redacted(self, span_id: str) -> Redacted:
        return next(each for each in self.redacted if each.span.id == span_id)

    def _drawn_elsewhere(
        self, engine: Engine, *, page: int, original: int
    ) -> tuple[DrawnElsewhere, dict[str, RestOfLine]]:
        on_page = [each for each in self.redacted if each.span.page == original]
        elsewhere = engine.redaction.drawn_elsewhere(
            page,
            self.kept[original],
            lines=self.edited[original].drawn,
            rests=[each.span for each in on_page],
            words=[run.text for each in on_page for run in each.runs],
        )
        rests: dict[str, RestOfLine] = {
            each.span.id: "removed" if each.rest is None else rest
            for each, rest in zip(on_page, elsewhere.rests, strict=True)
        }
        return elsewhere, rests

    def _blamed_on_new_text(self, left: _WordsLeft) -> set[str]:
        """New text writes no hidden copy, so a word found in one is the redaction's."""
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
    """Call it before the edits run: erasing can drop the fonts new text is measured with."""
    runs = runs_of(redacted)
    return RedactionController(
        tuple(redacted),
        covered=_covered_in(engine, resolved, runs),
        edited=edited,
        kept={
            page: engine.redaction.kept_by(page, edited[page].erased)
            for page in {run.page for run in runs}
        },
        hidden=engine.redaction.hidden_places(runs),
    )


def _key(run: Span) -> _RunKey:
    return (run.id, run.text)


def _covered_in(engine: Engine, resolved: Resolved, redacted: Sequence[Span]) -> frozenset[str]:
    pages_redacted = {span.page for span in redacted}
    on_redacted_pages = [
        listed.insert for listed in resolved.inserts if listed.insert.page in pages_redacted
    ]
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
    over = insert.page == span.page and box.overlaps(span.bbox)
    return over and any_word_left(span.text, insert.text)


def _drawn_box(engine: Engine, insert: Insert, *, turn_ccw: QuarterTurn) -> Rect:
    span = insert_span(engine, insert)
    width = engine.fit.measure(span, insert.text)
    x, y = insert.origin
    cos, sin = QUARTER_TURNS[turn_ccw]
    along_x, along_y = cos * width, -sin * width
    up_x, up_y = -sin * insert.size, -cos * insert.size
    xs = (x, x + along_x, x + up_x, x + along_x + up_x)
    ys = (y, y + along_y, y + up_y, y + along_y + up_y)
    return Rect(min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True, slots=True)
class _WordsLeft:
    in_box: list[Span]
    hidden: list[RedactedRun]
    elsewhere: list[RedactedRun]
    moved: list[RedactedRun]
    unchecked: list[RedactedRun]
    rests: dict[str, RestOfLine]

    @property
    def spans(self) -> list[Span]:
        return [*self.in_box, *self.hidden, *self.elsewhere]


def _words_left(
    engine: Engine,
    placed: list[RedactedRun],
    *,
    redacted: Sequence[RedactedRun],
    checked: RedactionController,
    pages: Mapping[int, int],
) -> _WordsLeft:
    """Render and export share it, so the warning and the refusal can't drift apart."""
    hidden = engine.redaction.still_hidden(placed)
    checked_pages = {
        page: checked._drawn_elsewhere(engine, page=page, original=pages[page])
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
    by_page: dict[int, list[RedactedRun]] = {}
    for run in runs:
        by_page.setdefault(run.page, []).append(run)
    for saved_page, original_page in enumerate(pages):
        for run in by_page.get(original_page, []):
            yield replace(run, page=saved_page)
