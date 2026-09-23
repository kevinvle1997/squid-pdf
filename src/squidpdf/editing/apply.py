"""Turn an edit log into engine calls.

The engine knows `remove` and `draw`. It does not know what a Replace is, which
is what keeps `core` free of any feature import. Translating one into the other
is this module's whole job.
"""

from __future__ import annotations

from collections.abc import Sequence

from squidpdf.core.engine import Engine
from squidpdf.core.types import Span, SpanIndex
from squidpdf.editing.edits import Edit, Insert, Redact, Replace
from squidpdf.editing.fit import FitCheck, options_for


def _collapse(edits: Sequence[Edit]) -> tuple[list[Replace | Redact], list[Insert]]:
    """Reduce a log to the last edit per span (order preserved), plus inserts.

    A span edited twice must only ever be drawn once, in its final state —
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


def apply(engine: Engine, edits: Sequence[Edit], index: SpanIndex) -> None:
    """Apply the whole log in memory, one span in its final state. Nothing is written.

    Removal happens in one pass before any redraw: PyMuPDF applies redactions
    per page, and a redaction applied after a redraw would erase the new text.
    """
    span_edits, to_insert = _collapse(edits)

    to_remove: list[Span] = []
    to_draw: list[tuple[Span, str]] = []

    for edit in span_edits:
        span = index.get(edit.span_id)
        if span is None:
            raise KeyError(f"no span {edit.span_id} in this index")

        to_remove.append(span)
        if isinstance(edit, Replace):
            to_draw.append((span, edit.text))

    if to_remove:
        engine.remove(to_remove)
    for span, text in to_draw:
        engine.draw(span, text)
    for ins in to_insert:
        engine.draw_at(ins.page, ins.origin, ins.text, ins.size, ins.color)


def check(engine: Engine, span: Span, text: str) -> FitCheck:
    """What would happen if the user typed this, with the ways out if it will not fit."""
    missing = engine.missing(span, text)
    original = engine.measure(span, span.text)
    delta = engine.measure(span, text) - original
    return FitCheck(
        delta_pt=round(delta, 2),
        missing=missing,
        options=options_for(delta, original),
    )


def verify_redactions(
    engine: Engine, edits: Sequence[Edit], index: SpanIndex
) -> dict[str, bool]:
    """Confirm each redacted string is really gone from the saved document.

    A covering rectangle would pass a visual check and fail this one, which is
    the entire point of running it. Only the last edit per span counts, matching
    what apply() actually drew — a Replace after a Redact means it was not
    redacted after all.
    """
    span_edits, _ = _collapse(edits)
    out: dict[str, bool] = {}
    for edit in span_edits:
        if not isinstance(edit, Redact):
            continue
        span = index.get(edit.span_id)
        if span is not None:
            out[edit.span_id] = engine.absent(span.text)
    return out
