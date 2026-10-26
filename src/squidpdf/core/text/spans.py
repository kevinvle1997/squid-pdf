"""From the pieces a page draws to the spans a person edits.

PDF writers split a sentence into pieces to adjust spacing, often a few letters each.
Unmerged, find-and-replace misses most matches and a click can land on half a word.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from squidpdf.core.constants import BASELINE_EPS, GAP_RATIO, SIZE_EPS
from squidpdf.core.types import (
    Fragment,
    Rect,
    Span,
    SpanIndex,
    TextPiece,
    index_of,
    same_font,
    span_id,
)

_POSITION_DP = 2  # boxes, origins and sizes, as span ids and the client see them
_KERNING_PT = -1.0  # a gap down to this far below 0 is kerning pulling letters together


def build_index(pages: Iterable[list[list[TextPiece]]]) -> SpanIndex:
    """Every editable span, from each page's lines of pieces, in order.

    Built once, from the pristine document, never from a patched one: that
    invariant is what keeps span ids stable. Re-extracting after an edit would
    change every id and dangle every reference the client holds.
    """
    # A blank group is no span, and takes no place in the count each id is made from.
    written = [(page, group) for page, group in _each_group(pages) if _text_in(group).strip()]
    return index_of(
        _span_from(page, group, ordinal=ordinal)
        for ordinal, (page, group) in enumerate(written)
    )


def _each_group(
    pages: Iterable[list[list[TextPiece]]],
) -> Iterator[tuple[int, list[TextPiece]]]:
    """Each group of pieces a span may be made from, and its page, in order."""
    for page, lines in enumerate(pages):
        for line in lines:
            for group in _merge(line):
                yield page, group


def _text_in(group: list[TextPiece]) -> str:
    """The text a group of pieces writes, as one."""
    return "".join(piece.text for piece in group)


def _merge(pieces: list[TextPiece]) -> list[list[TextPiece]]:
    """Group a line's pieces into spans, one per run of text in one font, size and paint."""
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
    if not same_font(previous, piece):
        return False
    if abs(previous.size - piece.size) > SIZE_EPS:
        return False
    # Another colour or opacity: a span is redrawn in one.
    if previous.color != piece.color or previous.opacity != piece.opacity:
        return False
    _x, previous_y = previous.origin
    _x, y = piece.origin
    if abs(previous_y - y) > BASELINE_EPS:
        return False
    # Close enough: a small negative gap is kerning pulling letters together.
    gap = piece.box.x0 - previous.box.x1
    return _KERNING_PT <= gap <= piece.size * GAP_RATIO


def _span_from(page: int, group: list[TextPiece], ordinal: int) -> Span:
    """Turn one merged group of pieces, not blank, into a Span."""
    text = _text_in(group)
    fragments = tuple(
        Fragment(text=piece.text, bbox=_round_box(piece.box), origin=_round_point(piece.origin))
        for piece in group
    )
    bbox = fragments[0].bbox
    for fragment in fragments[1:]:
        bbox = bbox.union(fragment.bbox)

    first = group[0]  # the span takes its style from its first piece
    return Span(
        id=span_id(page, bbox, font=first.font, text=text, ordinal=ordinal),
        page=page,
        text=text,
        font=first.font,
        font_object=first.font_object,
        size=round(first.size, _POSITION_DP),
        color=first.color,
        opacity=first.opacity,
        bbox=bbox,
        origin=fragments[0].origin,
        fragments=fragments,
        direction=first.direction,
    )


def _round_box(box: Rect) -> Rect:
    """The box to 2 decimals, as span ids and the client see it."""
    dp = _POSITION_DP
    return Rect(round(box.x0, dp), round(box.y0, dp), round(box.x1, dp), round(box.y1, dp))


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    """The point to 2 decimals, like its box."""
    x, y = point
    return (round(x, _POSITION_DP), round(y, _POSITION_DP))
