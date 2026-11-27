"""Room is read from the original page, so every edit of a span has the same."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field

import squidpdf.core.constants as constants
from squidpdf.core.types import Rect, Span


@dataclass(frozen=True, slots=True)
class _Placed[T]:
    box: Rect
    thing: T


@dataclass(frozen=True, slots=True, eq=False)
class _ByHeight[T]:
    by_top: list[_Placed[T]] = field(repr=False)
    tops: list[float] = field(repr=False)
    tallest: float
    tall: _ByHeight[T] | None = field(repr=False)

    def touching(self, band: Rect) -> Iterator[T]:
        first = bisect_left(self.tops, band.y0 - self.tallest)
        past = bisect_right(self.tops, band.y1)
        for placed in self.by_top[first:past]:
            if _touches(placed.box, band):
                yield placed.thing
        if self.tall is not None:
            yield from self.tall.touching(band)


def _by_height[T](short: list[_Placed[T]], *, tall: list[_Placed[T]]) -> _ByHeight[T]:
    by_top = sorted(short, key=lambda placed: placed.box.y0)
    return _ByHeight(
        by_top,
        tops=[placed.box.y0 for placed in by_top],
        tallest=max((placed.box.height for placed in by_top), default=0.0),
        tall=_by_height(tall, tall=[]) if tall else None,
    )


@dataclass(frozen=True, slots=True, eq=False)
class _Walls:
    text: _ByHeight[Span] = field(repr=False)
    art: _ByHeight[Rect] = field(repr=False)
    margin: float

    def room_of(self, span: Span, *, besides: frozenset[str] = frozenset()) -> float:
        box = span.bbox
        text_ahead = [
            other.bbox.x0
            for other in self.text.touching(box)
            if other.id not in besides and _is_ahead(other, span)
        ]
        art_ahead = [
            _stop_at(drawn, box) for drawn in self.art.touching(box) if _not_behind(drawn, box)
        ]
        walls = [*text_ahead, self.margin, *art_ahead]
        return max(min(walls) - box.x1, 0.0)


def _walls_on(spans: list[Span], drawn: list[Rect]) -> _Walls | None:
    level_spans = [span for span in spans if not span.turned]
    if not level_spans:
        return None
    turned = [_Placed(span.bbox, span) for span in spans if span.turned]
    text = _by_height([_Placed(span.bbox, span) for span in level_spans], tall=turned)
    return _Walls(text, _art_near(drawn, text), margin=max(span.bbox.x1 for span in spans))


def rooms_on(spans: list[Span], drawn: list[Rect], *, wanted: set[str]) -> dict[str, float]:
    rooms = {span.id: 0.0 for span in spans if span.id in wanted}
    walls = _walls_on(spans, drawn)
    if walls is None:
        return rooms
    measured = {
        span.id: walls.room_of(span) for span in spans if span.id in wanted and not span.turned
    }
    return rooms | measured


def line_rooms_on(
    spans: list[Span], drawn: list[Rect], *, lines: list[list[Span]]
) -> list[float]:
    """An earlier, taller span can meet what the line's last one passes under."""
    walls = _walls_on(spans, drawn)
    if walls is None:
        return [0.0 for _line in lines]
    return [_line_room(line, walls) for line in lines]


def _line_room(line: list[Span], walls: _Walls) -> float:
    own = frozenset(span.id for span in line)
    return min(walls.room_of(span, besides=own) for span in line)


def _art_near(drawn: list[Rect], text: _ByHeight[Span]) -> _ByHeight[Rect]:
    tall_from = constants.ROOM_TALL_ART_LINES * text.tallest
    short = [_Placed(box, box) for box in drawn if box.height <= tall_from]
    tall = [_Placed(box, box) for box in drawn if box.height > tall_from]
    near_text = [placed for placed in tall if _reaches_text(placed.box, text)]
    return _by_height(short, tall=near_text)


def _reaches_text(box: Rect, text: _ByHeight[Span]) -> bool:
    return next(text.touching(box), None) is not None


def _is_ahead(other: Span, span: Span) -> bool:
    """One on its own line starting a little inside its end, as kerned, is still ahead."""
    box = span.bbox
    past_its_end = other.bbox.x0 >= box.x1 - constants.ROOM_TOUCH_PT
    kerned_in = (
        _same_line(other.bbox, box) and other.bbox.x0 > box.x0 and other.bbox.x1 > box.x1
    )
    return other is not span and (past_its_end or kerned_in)


def _not_behind(drawn: Rect, box: Rect) -> bool:
    return drawn.x1 >= box.x1 - constants.ROOM_TOUCH_PT


def _stop_at(drawn: Rect, box: Rect) -> float:
    return drawn.x0 if drawn.x0 >= box.x1 - constants.ROOM_TOUCH_PT else drawn.x1


def _touches(a: Rect, b: Rect) -> bool:
    return min(a.y1, b.y1) - max(a.y0, b.y0) > constants.ROOM_TOUCH_PT


def _same_line(a: Rect, b: Rect) -> bool:
    shared = min(a.y1, b.y1) - max(a.y0, b.y0)
    return shared > min(a.height, b.height) / 2
