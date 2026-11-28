"""Unmerged, find-and-replace misses most matches and a click can land on half a word."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import squidpdf.core.constants as constants
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

_POSITION_DP = 2


def build_index(pages: Iterable[list[list[TextPiece]]]) -> SpanIndex:
    written = list(_each_group(pages))
    return index_of(
        _span_from(page, group, ordinal=ordinal)
        for ordinal, (page, group) in enumerate(written)
    )


def _each_group(
    pages: Iterable[list[list[TextPiece]]],
) -> Iterator[tuple[int, list[TextPiece]]]:
    for page, lines in enumerate(pages):
        for line in lines:
            groups = _merge(line)
            worded = [index for index, group in enumerate(groups) if _text_in(group).strip()]
            for index, group in enumerate(groups):
                # A space in a font of its own, as Chrome writes them, is kept between words.
                if worded and worded[0] <= index <= worded[-1]:
                    yield page, group


def _text_in(group: list[TextPiece]) -> str:
    return "".join(piece.text for piece in group)


def _merge(pieces: list[TextPiece]) -> list[list[TextPiece]]:
    if not pieces:
        return []
    groups = [[pieces[0]]]
    for piece in pieces[1:]:
        current_group = groups[-1]
        last_piece = current_group[-1]
        if _continues(last_piece, piece):
            current_group.append(piece)
        else:
            groups.append([piece])
    return groups


def _continues(previous: TextPiece, piece: TextPiece) -> bool:
    if not same_font(previous, piece):
        return False
    if abs(previous.size - piece.size) > constants.SIZE_EPS:
        return False
    # Another colour or opacity: a span is redrawn in one.
    if previous.color != piece.color or previous.opacity != piece.opacity:
        return False
    _x, previous_y = previous.origin
    _x, y = piece.origin
    if abs(previous_y - y) > constants.BASELINE_EPS:
        return False
    gap = piece.box.x0 - previous.box.x1
    return constants.KERNING_PT <= gap <= piece.size * constants.GAP_RATIO


def _span_from(page: int, group: list[TextPiece], ordinal: int) -> Span:
    text = _text_in(group)
    fragments = tuple(
        Fragment(text=piece.text, bbox=_round_box(piece.box), origin=_round_point(piece.origin))
        for piece in group
    )
    bbox = fragments[0].bbox
    for fragment in fragments[1:]:
        bbox = bbox.union(fragment.bbox)

    first = group[0]
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
    dp = _POSITION_DP
    return Rect(round(box.x0, dp), round(box.y0, dp), round(box.x1, dp), round(box.y1, dp))


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    x, y = point
    return (round(x, _POSITION_DP), round(y, _POSITION_DP))
