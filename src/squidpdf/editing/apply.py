"""Turn an edit log into engine calls.

The engine knows `remove` and `draw`. It does not know what a Replace is, which
is what keeps `core` free of any feature import. Translating one into the other
is this module's whole job.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from squidpdf.core import words
from squidpdf.core.engine import Engine
from squidpdf.core.types import Span, SpanIndex
from squidpdf.editing.edits import Edit, Insert, Redact, Replace
from squidpdf.editing.fit import FitCheck, options_for
from squidpdf.editing.types import Skipped, Strategy

_BAD_REFERENCE = "bad_reference"


class BadReference(Exception):
    """A redaction points at text that isn't there. Skipping it would be a leak."""

    def __init__(self, span_id: str) -> None:
        """Name the span the redaction asked for."""
        super().__init__(span_id)
        self.span_id = span_id


def _collapse(edits: Sequence[Edit]) -> tuple[list[Replace | Redact], list[Insert]]:
    """Reduce a log to the last edit per span (order preserved), plus inserts.

    A span edited twice must only ever be drawn once, in its final state:
    otherwise a second correction draws on top of the first instead of
    replacing it, and a Redact after a Replace would leave the replacement text
    visible while still being reported as gone.
    """
    latest: dict[str, Replace | Redact] = {}
    order: list[str] = []
    inserts: list[Insert] = []

    for edit in edits:
        if isinstance(edit, Insert):
            inserts.append(edit)
            continue
        if edit.span_id not in latest:
            order.append(edit.span_id)
        latest[edit.span_id] = edit

    return [latest[span_id] for span_id in order], inserts


def _resolve(
    engine: Engine, edits: Sequence[Edit], index: SpanIndex
) -> tuple[list[tuple[Replace | Redact, Span]], list[Insert], list[Skipped]]:
    """The log collapsed, each span edit with its span, its inserts, and what points at nothing.

    A redaction that points at nothing raises BadReference instead.
    """
    page_count = len(engine.pages()) if any(isinstance(e, Insert) for e in edits) else 0
    spans: dict[str, Span] = {}
    kept: list[Edit] = []
    skipped: list[Skipped] = []
    for i, edit in enumerate(edits):
        if isinstance(edit, Insert):
            if 0 <= edit.page < page_count:
                kept.append(edit)
            else:
                skipped.append(Skipped(i, _BAD_REFERENCE, words.NO_PAGE))
            continue
        span = index.get(edit.span_id)
        if span is not None:
            spans[span.id] = span
            kept.append(edit)
        elif isinstance(edit, Redact):
            raise BadReference(edit.span_id)
        else:
            skipped.append(Skipped(i, _BAD_REFERENCE, words.NO_SPAN))
    span_edits, inserts = _collapse(kept)
    return [(e, spans[e.span_id]) for e in span_edits], inserts, skipped


def apply(
    engine: Engine,
    edits: Sequence[Edit],
    index: SpanIndex,
    pages: Collection[int] | None = None,
) -> list[Skipped]:
    """Apply the log in memory, one span in its final state. Nothing is written.

    Only edits on `pages` are drawn, or on every page when it's None. Edits that
    point at nothing are left out and returned. Removal happens in one pass
    before any redraw: PyMuPDF applies redactions per page, and a redaction
    applied after a redraw would erase the new text.
    """
    span_edits, inserts, skipped = _resolve(engine, edits, index)

    to_remove: list[Span] = []
    to_draw: list[tuple[Span, str, float | None, float]] = []
    for edit, span in span_edits:
        if pages is not None and span.page not in pages:
            continue
        to_remove.append(span)
        if isinstance(edit, Replace):
            # Worked out before remove(), which can drop the fonts it measures with.
            size, scale_x = _drawn_at(engine, span, edit)
            to_draw.append((span, edit.text, size, scale_x))

    if to_remove:
        engine.remove(to_remove)
    for span, text, size, scale_x in to_draw:
        engine.draw(span, text, size, scale_x)
    for ins in inserts:
        if pages is None or ins.page in pages:
            engine.draw_at(ins.page, ins.origin, ins.text, ins.size, ins.color)
    return skipped


def _drawn_at(engine: Engine, span: Span, edit: Replace) -> tuple[float | None, float]:
    """The size and horizontal scale the edit's strategy draws at, if it's offered."""
    strategy = check(engine, span, edit.text, edit.strategy).strategy
    if strategy == "as-is":
        return None, 1.0
    # Width is linear in both, so this ratio lands the text on the original's end.
    ratio = engine.measure(span, span.text) / engine.measure(span, edit.text)
    return (span.size * ratio, 1.0) if strategy == "shrink" else (None, ratio)


def fits(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> dict[str, FitCheck]:
    """A fit for each span the log leaves replaced, drawn or not. Measurement only."""
    span_edits, _inserts, _skipped = _resolve(engine, edits, index)
    return {
        span.id: check(engine, span, edit.text, edit.strategy)
        for edit, span in span_edits
        if isinstance(edit, Replace)
    }


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
