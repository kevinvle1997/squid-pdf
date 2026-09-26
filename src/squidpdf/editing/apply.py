"""Turn an edit log into engine calls.

The engine knows `remove` and `draw`. It does not know what a Replace is, which
is what keeps `core` free of any feature import. Translating one into the other
is this module's whole job.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from squidpdf.core import words
from squidpdf.core.engine import Engine
from squidpdf.core.fonts import BUILT_IN, drawn_in
from squidpdf.core.types import Span, SpanIndex, new_text
from squidpdf.editing.edits import Edit, Insert, Redact, Replace
from squidpdf.editing.fit import FitCheck, options_for
from squidpdf.editing.types import Applied, Notice, Skipped, Strategy

_BAD_REFERENCE = "bad_reference"


class BadReference(Exception):
    """A redaction points at text that isn't there. Skipping it would be a leak."""

    def __init__(self, span_id: str) -> None:
        """Name the span the redaction asked for."""
        super().__init__(span_id)
        self.span_id = span_id


def _collapse(edits: Sequence[Replace | Redact]) -> list[Replace | Redact]:
    """Reduce a log's span edits to the last edit per span, order preserved.

    A span edited twice must only ever be drawn once, in its final state:
    otherwise a second correction draws on top of the first instead of
    replacing it, and a Redact after a Replace would leave the replacement text
    visible while still being reported as gone.
    """
    # Overwriting a key keeps its place, so spans stay in the order first edited.
    latest: dict[str, Replace | Redact] = {}
    for edit in edits:
        latest[edit.span_id] = edit
    return list(latest.values())


def _undone_redactions(edits: Sequence[Edit]) -> list[str]:
    """The spans a Replace brought back after they were redacted: the last edit wins."""
    redacted: set[str] = set()
    undone: dict[str, None] = {}  # a dict, to keep the order and drop repeats
    for edit in edits:
        if isinstance(edit, Redact):
            redacted.add(edit.span_id)
            undone.pop(edit.span_id, None)  # redacted again: the redaction holds
        elif isinstance(edit, Replace) and edit.span_id in redacted:
            undone[edit.span_id] = None
    return list(undone)


def _resolve(
    engine: Engine, edits: Sequence[Edit], index: SpanIndex
) -> tuple[list[tuple[Replace | Redact, Span]], list[tuple[int, Insert]], list[Skipped]]:
    """The log collapsed, each span edit with its span, each insert with its place, and what
    points at nothing.

    A redaction that points at nothing raises BadReference instead.
    """
    # Only inserts need the page count, so skip reading the pages without one.
    has_inserts = any(isinstance(e, Insert) for e in edits)
    page_count = len(engine.pages()) if has_inserts else 0
    spans: dict[str, Span] = {}
    kept: list[Replace | Redact] = []
    inserts: list[tuple[int, Insert]] = []
    skipped: list[Skipped] = []
    for position, edit in enumerate(edits):
        # An insert on a page the document doesn't have.
        if isinstance(edit, Insert) and not 0 <= edit.page < page_count:
            skipped.append(Skipped(position, _BAD_REFERENCE, words.NO_PAGE))
            continue
        # An insert on a real page.
        if isinstance(edit, Insert):
            inserts.append((position, edit))
            continue
        # The browser can send an id this document doesn't have.
        span = index.get(edit.span_id)
        # A redaction of missing text: skipping it would be a leak.
        if span is None and isinstance(edit, Redact):
            raise BadReference(edit.span_id)
        # A replace of missing text.
        if span is None:
            skipped.append(Skipped(position, _BAD_REFERENCE, words.NO_SPAN))
            continue
        spans[span.id] = span
        kept.append(edit)
    span_edits = _collapse(kept)
    return [(e, spans[e.span_id]) for e in span_edits], inserts, skipped


def apply(
    engine: Engine,
    edits: Sequence[Edit],
    index: SpanIndex,
    pages: Collection[int] | None = None,
) -> Applied:
    """Apply the log in memory, one span in its final state. Nothing is written.

    Only edits on `pages` are drawn, or on every page when it's None. Returns the
    edits that point at nothing, left out, and notices for any drawn other than
    asked. Removal happens in one pass before any redraw: PyMuPDF applies
    redactions per page, and a redaction applied after a redraw would erase the
    new text.
    """
    span_edits, inserts, skipped = _resolve(engine, edits, index)

    to_remove: list[Span] = []
    to_draw: list[tuple[Span, str, float | None, float]] = []
    for edit, span in span_edits:
        off_screen = pages is not None and span.page not in pages
        if off_screen:
            continue
        to_remove.append(span)
        if isinstance(edit, Replace):
            # Worked out before remove(), which can drop the fonts it measures with.
            size, scale_x = _drawn_at(engine, span, edit)
            to_draw.append((span, edit.text, size, scale_x))

    if to_remove:
        engine.remove(to_remove)
    notices = [Notice(span_id, words.REDACTION_UNDONE) for span_id in _undone_redactions(edits)]
    for span, text, size, scale_x in to_draw:
        for detail in engine.draw(span, text, size, scale_x):
            notices.append(Notice(span.id, detail))
    for position, insert in inserts:
        on_screen = pages is None or insert.page in pages
        if on_screen:
            for detail in engine.draw(_insert_span(insert), insert.text):
                notices.append(Notice(None, detail, edit=position))
    return Applied(skipped, notices)


def _insert_span(insert: Insert) -> Span:
    """An insert as a span, so it's judged, measured and drawn exactly like an edit."""
    return new_text(
        insert.page, insert.origin, insert.text, insert.size, insert.font, insert.color
    )


def _drawn_at(engine: Engine, span: Span, edit: Replace) -> tuple[float | None, float]:
    """The font size and horizontal stretch to draw a replacement at.

    A None size keeps the span's own. The edit's strategy counts only if it was offered.
    """
    strategy = check(engine, span, edit.text, edit.strategy).strategy
    # Drawn as typed: the span's size, no stretch.
    if strategy == "as-is":
        return None, 1.0
    # Width is linear in both, so this ratio lands the text on the original's end.
    ratio = engine.measure(span, span.text) / engine.measure(span, edit.text)
    # Smaller letters, same shape.
    if strategy == "shrink":
        return span.size * ratio, 1.0
    # Condense: same size, letters squeezed narrower.
    return None, ratio


def fits(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> dict[str, FitCheck]:
    """A fit for each span the log leaves replaced, drawn or not. Measurement only."""
    span_edits, _inserts, _skipped = _resolve(engine, edits, index)
    return {
        span.id: check(engine, span, edit.text, edit.strategy)
        for edit, span in span_edits
        if isinstance(edit, Replace)
    }


def insert_fits(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> dict[int, FitCheck]:
    """A fit for each insert, by its place in the log. Measurement only."""
    _span_edits, inserts, _skipped = _resolve(engine, edits, index)
    return {position: check_insert(engine, insert) for position, insert in inserts}


def check_insert(engine: Engine, insert: Insert) -> FitCheck:
    """What new text will really look like: in its chosen font, or what draws it instead.

    Nothing to fit against, so only the font and the letters are checked.
    """
    span = _insert_span(insert)
    [report] = engine.assess(SpanIndex([span]))
    built_in = insert.font in BUILT_IN
    # Chosen from the document, and the file's copy can't be used here at all.
    unusable = not built_in and report.why not in (None, words.FONT_LACKS_LETTERS)
    return FitCheck(
        delta_pt=0.0,
        missing=[] if unusable else engine.missing(span, insert.text),
        left_out=engine.left_out(span, insert.text),
        stand_in=drawn_in(insert.font),
        unavailable=insert.font if unusable else "",
    )


def check(engine: Engine, span: Span, text: str, strategy: Strategy = "as-is") -> FitCheck:
    """What would happen if the user typed this, with the ways out if it will not fit.

    `strategy` is kept only if it's one of the ways out offered; otherwise as-is.
    """
    missing = engine.missing(span, text)
    original = engine.measure(span, span.text)
    delta = engine.measure(span, text) - original
    options = options_for(delta, original)
    offered = strategy in {o.name for o in options}
    return FitCheck(
        delta_pt=round(delta, 2),
        missing=missing,
        options=options,
        strategy=strategy if offered else "as-is",
        left_out=engine.left_out(span, text),
        stand_in=drawn_in(span.font),
        asked=strategy,
    )


def verify_redactions(
    engine: Engine, edits: Sequence[Edit], index: SpanIndex
) -> dict[str, bool]:
    """Confirm each redacted string is really gone from the saved document.

    A covering rectangle would pass a visual check and fail this one, which is
    the entire point of running it. Only the last edit per span counts, matching
    what apply() actually drew. A Replace after a Redact means it was not
    redacted after all. A redaction of an unknown span raises, as in apply().
    """
    span_edits, _inserts, _skipped = _resolve(engine, edits, index)
    return {
        span.id: engine.absent(span.text)
        for edit, span in span_edits
        if isinstance(edit, Redact)
    }
