"""How far a line can run to the right before it reaches anything on its page: its room.

A replacement is too long only past its room (`editing/fit.py`). Room is read
from the original page, so every edit of a span has the same.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field

from squidpdf.core.constants import ROOM_TALL_ART_LINES, ROOM_TOUCH_PT
from squidpdf.core.types import Rect, Span


@dataclass(frozen=True, slots=True, eq=False)
class _ByHeight[T]:
    """Things on a page by their box, to find those level with a band by halving down it."""

    by_top: list[tuple[Rect, T]] = field(repr=False)  # top first
    tops: list[float] = field(repr=False)  # each one's top, in that order
    tallest: float  # the tallest of `by_top`: none starting further up can reach a band
    # Taller things, apart, so their height doesn't widen the search for the rest.
    tall: _ByHeight[T] | None = field(repr=False)

    def touching(self, band: Rect) -> Iterator[T]:
        """Everything whose height overlaps `band`'s at all."""
        first = bisect_left(self.tops, band.y0 - self.tallest)
        past = bisect_right(self.tops, band.y1)
        for box, thing in self.by_top[first:past]:
            if _touches(box, band):
                yield thing
        if self.tall is not None:
            yield from self.tall.touching(band)


def _by_height[T](short: list[tuple[Rect, T]], *, tall: list[tuple[Rect, T]]) -> _ByHeight[T]:
    """`short` things to search by height, and `tall` ones apart, searched the same way."""
    by_top = sorted(short, key=lambda entry: entry[0].y0)
    return _ByHeight(
        by_top,
        tops=[box.y0 for box, _thing in by_top],
        tallest=max((box.height for box, _thing in by_top), default=0.0),
        tall=_by_height(tall, tall=[]) if tall else None,
    )


def rooms_on(spans: list[Span], drawn: list[Rect], *, wanted: set[str]) -> dict[str, float]:
    """The room of each `wanted` span on one page, in points; `drawn` is its line art.

    Up to the nearest text or drawing ahead of it that it would overlap, and never
    past the page's rightmost text. A turned span has none.
    """
    rooms = {span.id: 0.0 for span in spans if span.id in wanted}
    level_spans = [span for span in spans if not span.turned]
    if not level_spans:
        return rooms
    turned = [(span.bbox, span) for span in spans if span.turned]
    text = _by_height([(span.bbox, span) for span in level_spans], tall=turned)
    art = _art_near(drawn, text)
    margin = max(span.bbox.x1 for span in spans)
    measured = {
        span.id: _room_of(span, text=text, art=art, margin=margin)
        for span in level_spans
        if span.id in wanted
    }
    return rooms | measured


def _room_of(
    span: Span, *, text: _ByHeight[Span], art: _ByHeight[Rect], margin: float
) -> float:
    """How far `span` can run right before the text or drawing ahead of it, or the margin."""
    walls = [*_text_ahead(span, text), margin, *_drawn_ahead(span, art)]
    return max(min(walls) - span.bbox.x1, 0.0)


def _art_near(drawn: list[Rect], text: _ByHeight[Span]) -> _ByHeight[Rect]:
    """The line art level with some text, to search by height: a chart's lines are many.

    A piece taller than a few of the tallest lines (a frame, a rule down a column) is
    searched apart, once it's known to reach some text.
    """
    tall_from = ROOM_TALL_ART_LINES * text.tallest
    short = [(box, box) for box in drawn if box.height <= tall_from]
    tall = [(box, box) for box in drawn if box.height > tall_from]
    near_text = [entry for entry in tall if any(True for _span in text.touching(entry[0]))]
    return _by_height(short, tall=near_text)


def _text_ahead(span: Span, text: _ByHeight[Span]) -> Iterator[float]:
    """Where each span it touches starts, of those after it.

    One on its own line that starts a little inside its end, as a kern sets it, is
    still ahead; one on a line above or below must start past its end.
    """
    box = span.bbox
    for other in text.touching(box):
        past_its_end = other.bbox.x0 >= box.x1 - ROOM_TOUCH_PT
        kerned_in = (
            _same_line(other.bbox, box) and other.bbox.x0 > box.x0 and other.bbox.x1 > box.x1
        )
        if other is not span and (past_its_end or kerned_in):
            yield other.bbox.x0


def _drawn_ahead(span: Span, art_near: _ByHeight[Rect]) -> Iterator[float]:
    """Where each drawing to the span's right that it touches would stop it."""
    box = span.bbox
    for art in art_near.touching(box):
        # Left of the span's end altogether: behind it, not ahead.
        if art.x1 < box.x1 - ROOM_TOUCH_PT:
            continue
        # Ahead of it, its near side; around it, as a cell or a box it's in, its far side.
        yield art.x0 if art.x0 >= box.x1 - ROOM_TOUCH_PT else art.x1


def _touches(a: Rect, b: Rect) -> bool:
    """Whether two boxes' heights overlap at all."""
    return min(a.y1, b.y1) - max(a.y0, b.y0) > ROOM_TOUCH_PT


def _same_line(a: Rect, b: Rect) -> bool:
    """Whether two boxes' heights overlap by more than half the shorter's."""
    shared = min(a.y1, b.y1) - max(a.y0, b.y0)
    return shared > min(a.height, b.height) / 2
