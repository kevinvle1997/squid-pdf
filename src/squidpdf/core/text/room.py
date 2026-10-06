"""How far a line can run to the right before it reaches anything on its page: its room.

A replacement is too long only past its room (`editing/fit.py`). Room is read
from the original page, so every edit of a span has the same.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field

from squidpdf.core.constants import (
    ROOM_BLOCK_STEP_LINES,
    ROOM_COLUMN_EM,
    ROOM_EDGE_PT,
    ROOM_LEADING_SLACK,
    ROOM_TALL_ART_LINES,
    ROOM_TOUCH_PT,
)
from squidpdf.core.types import Rect, Span


@dataclass(frozen=True, slots=True)
class _Line:
    """The level spans of one column on one line of a page, left to right, and their box."""

    spans: list[Span]
    box: Rect


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

    The nearest text to its right that it touches; with none, its block's widest
    line, or nothing in justified text, short of any column beside the block. Never
    past a drawing it would reach, nor the page's rightmost text. A turned span has none.
    """
    rooms = {span.id: 0.0 for span in spans if span.id in wanted}
    level_spans = [span for span in spans if not span.turned]
    if not level_spans:
        return rooms
    turned = [(span.bbox, span) for span in spans if span.turned]
    text = _by_height([(span.bbox, span) for span in level_spans], tall=turned)
    art = _art_near(drawn, text)
    margin = max(span.bbox.x1 for span in spans)
    for block in _blocks_of(_lines_of(level_spans)):
        # No span asked for in it: its edges needn't be worked out.
        if not any(span.id in wanted for line in block for span in line.spans):
            continue
        for line, edge in _edges_of(block, text):
            for span in line.spans:
                if span.id not in wanted:
                    continue
                # Text that follows on the line is what it'd run into; else, its block's edge.
                text_starts = list(_text_ahead(span, text))
                walls = [*(text_starts or [edge]), margin, *_drawn_ahead(span, art)]
                rooms[span.id] = max(min(walls) - span.bbox.x1, 0.0)
    return rooms


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


def _edges_of(block: list[_Line], text: _ByHeight[Span]) -> Iterator[tuple[_Line, float]]:
    """Each line of `block`, and where it may run to with nothing after it on the line.

    The block's widest line, or the line's own end in justified text, short of the
    nearest text beside the block: another column.
    """
    justified = _justified(block)
    widest = max(line.box.x1 for line in block)
    band = Rect(block[0].box.x0, block[0].box.y0, widest, block[-1].box.y1)
    own = {span.id for line in block for span in line.spans}
    beside = sorted(
        other.bbox.x0
        for other in text.touching(band)
        if other.id not in own and other.bbox.x0 > block[0].box.x0
    )
    for line in block:
        # Past the block's widest line reads as wrong even in empty space; justified,
        # past its own end breaks the edge it shares.
        edge = line.box.x1 if justified else widest
        # A column beside the block: its nearest text ahead of the line's end stops it.
        at = bisect_left(beside, line.box.x1 - ROOM_TOUCH_PT)
        nearest_beside = beside[at] if at < len(beside) else edge
        yield line, min(edge, nearest_beside)


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


def _lines_of(spans: list[Span]) -> list[_Line]:
    """The spans grouped into lines, top first: a span joins the first line it half overlaps."""
    tallest = max(span.bbox.height for span in spans)
    rows: list[list[Span]] = []
    row_tops: list[float] = []  # each row's first span's top, in order, as spans come top first
    for span in sorted(spans, key=lambda s: (s.bbox.y0, s.bbox.x0)):
        # Only a row starting within the tallest span above it can reach it.
        nearby = rows[bisect_left(row_tops, span.bbox.y0 - tallest) :]
        row = next((row for row in nearby if _same_line(row[0].bbox, span.bbox)), None)
        if row is None:
            rows.append([span])
            row_tops.append(span.bbox.y0)
        else:
            row.append(span)
    return [line for row in rows for line in _columns_of(sorted(row, key=lambda s: s.bbox.x0))]


def _same_line(a: Rect, b: Rect) -> bool:
    """Whether two boxes' heights overlap by more than half the shorter's."""
    shared = min(a.y1, b.y1) - max(a.y0, b.y0)
    return shared > min(a.height, b.height) / 2


def _columns_of(row: list[Span]) -> list[_Line]:
    """A row of spans, left to right, cut into one line per column at each wide gap."""
    columns = [[row[0]]]
    column_end = row[0].bbox.x1
    for span in row[1:]:
        # A wide gap: another column, whose lines make blocks of their own.
        if span.bbox.x0 - column_end > ROOM_COLUMN_EM * span.size:
            columns.append([span])
            column_end = span.bbox.x1
            continue
        columns[-1].append(span)
        column_end = max(column_end, span.bbox.x1)
    return [_line_of(column) for column in columns]


def _line_of(spans: list[Span]) -> _Line:
    """One column's spans on a line, and the box around them."""
    left = spans[0].bbox.x0
    right = max(span.bbox.x1 for span in spans)
    top = min(span.bbox.y0 for span in spans)
    bottom = max(span.bbox.y1 for span in spans)
    return _Line(spans, Rect(left, top, right, bottom))


def _blocks_of(lines: list[_Line]) -> list[list[_Line]]:
    """Runs of lines, top first, that share a left edge and step down at one leading."""
    blocks: list[list[_Line]] = []
    for line in lines:
        block = next((block for block in blocks if _continues(block, line)), None)
        if block is None:
            blocks.append([line])
        else:
            block.append(line)
    return blocks


def _continues(block: list[_Line], line: _Line) -> bool:
    """Whether `line` is the next line of `block`: the same left edge, the same leading."""
    last = block[-1].box
    step = line.box.y0 - last.y0
    same_edge = abs(line.box.x0 - block[0].box.x0) <= ROOM_EDGE_PT
    near = 0 < step <= ROOM_BLOCK_STEP_LINES * last.height
    if not (same_edge and near):
        return False
    # A second line sets the leading; a third must keep it.
    if len(block) == 1:
        return True
    leading = last.y0 - block[-2].box.y0
    return abs(step - leading) <= ROOM_LEADING_SLACK * leading


def _justified(block: list[_Line]) -> bool:
    """Whether every line but the last ends at one edge: three lines or more set it."""
    ends = [line.box.x1 for line in block[:-1]]
    # One line's end is no edge.
    if len(ends) <= 1:
        return False
    return max(ends) - min(ends) <= ROOM_EDGE_PT
