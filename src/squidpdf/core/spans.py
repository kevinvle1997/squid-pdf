"""From the pieces a page draws to the spans a person edits.

PDF writers split a sentence into many pieces to adjust letter spacing, so a
piece is often a few letters and sometimes half a word. Without merging,
find-and-replace misses most real matches and the user can click something
that is not a whole word. Pure functions: no PDF is opened here.
"""

from __future__ import annotations

from collections.abc import Iterable

from squidpdf.core.constants import BASELINE_EPS, GAP_RATIO, SIZE_EPS
from squidpdf.core.types import Fragment, Rect, Span, SpanIndex, TextPiece, span_id

_POSITION_DP = 2  # boxes, origins and sizes, as span ids and the client see them
_KERNING_PT = -1.0  # a gap down to this far below 0 is kerning pulling letters together


def build_index(pages: Iterable[list[list[TextPiece]]]) -> SpanIndex:
    """Every editable span, from each page's lines of pieces, in order.

    Built once, from the pristine document, never from a patched one: that
    invariant is what keeps span ids stable. Re-extracting after an edit would
    change every id and dangle every reference the client holds.
    """
    spans: list[Span] = []
    for page, lines in enumerate(pages):
        for line in lines:
            for group in merge(line):
                span = _span(page, group, ordinal=len(spans))
                if span is not None:
                    spans.append(span)
    return SpanIndex(spans)


def merge(pieces: list[TextPiece]) -> list[list[TextPiece]]:
    """Group a line's pieces into spans, one per run of same-style text."""
    if not pieces:
        return []
    groups = [[pieces[0]]]
    for piece in pieces[1:]:
        current_group = groups[-1]  # the newest group, the one still being built
        last_piece = current_group[-1]  # the piece just before this one on the line
        if _continues(last_piece, piece):
            current_group.append(piece)
        else:
            groups.append([piece])
    return groups


def _continues(previous: TextPiece, piece: TextPiece) -> bool:
    """Whether `piece` carries on the span that `previous` ends."""
    # Another font or size.
    if previous.font != piece.font:
        return False
    if abs(previous.size - piece.size) > SIZE_EPS:
        return False
    # Another baseline.
    _x, previous_y = previous.origin
    _x, y = piece.origin
    if abs(previous_y - y) > BASELINE_EPS:
        return False
    # Close enough: a small negative gap is kerning pulling letters together.
    gap = piece.box.x0 - previous.box.x1
    return _KERNING_PT <= gap <= piece.size * GAP_RATIO


def _span(page: int, group: list[TextPiece], ordinal: int) -> Span | None:
    """Turn one merged group of pieces into a Span, or None if blank."""
    text = "".join(piece.text for piece in group)
    if not text.strip():
        return None

    fragments = tuple(
        Fragment(text=piece.text, bbox=_round_box(piece.box), origin=_round_point(piece.origin))
        for piece in group
    )
    bbox = fragments[0].bbox
    for fragment in fragments[1:]:
        bbox = bbox.union(fragment.bbox)

    first = group[0]  # the span takes its style from its first piece
    return Span(
        id=span_id(page, bbox, first.font, text, ordinal),
        page=page,
        text=text,
        font=first.font,
        size=round(first.size, _POSITION_DP),
        color=first.color,
        bbox=bbox,
        origin=fragments[0].origin,
        fragments=fragments,
    )


def _round_box(box: Rect) -> Rect:
    """The box to 2 decimals, as span ids and the client see it."""
    dp = _POSITION_DP
    return Rect(round(box.x0, dp), round(box.y0, dp), round(box.x1, dp), round(box.y1, dp))


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    """The point to 2 decimals, like its box."""
    x, y = point
    return (round(x, _POSITION_DP), round(y, _POSITION_DP))
