"""Which spans a page draws as one line, to edit as one: one baseline, a word space apart.

A sentence with one word in bold is three spans. Edited apart, each has almost no room,
since a span's room ends where the next one starts; edited as one line, its runs keep
their own style, and those after a change move along with it.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator

import squidpdf.core.constants as constants
from squidpdf.core.types import Rect, Span


def lines_on(spans: list[Span], *, breaks: list[Rect], in_fields: set[str]) -> list[list[Span]]:
    """Each line of two spans or more on one page, its spans left to right, top line first.

    `breaks` are the page's drawings and links: one level with a line, with an edge inside
    it, cuts it there, since a span moved would leave it behind. Spans in `in_fields`, which
    a form field draws, and any span another overlaps, as a shadow or a word drawn twice,
    never join one.
    """
    level = [span for span in spans if not span.turned]
    apart = in_fields | _overlapped(level)
    joinable = [span for span in level if span.id not in apart]
    chains = [chain for baseline in _each_baseline(joinable) for chain in _chains(baseline)]
    lines = [cut for chain in chains if len(chain) > 1 for cut in _cut(chain, breaks=breaks)]
    return [line for line in lines if len(line) > 1]


def _overlapped(spans: list[Span]) -> set[str]:
    """The ids of spans another one overlaps on its line, by more than a kern."""
    by_top = sorted(spans, key=lambda span: span.bbox.y0)
    tops = [span.bbox.y0 for span in by_top]
    tallest = max((span.bbox.height for span in spans), default=0.0)
    overlapped: set[str] = set()
    for span in spans:
        first = bisect_left(tops, span.bbox.y0 - tallest)
        past = bisect_right(tops, span.bbox.y1)
        near = by_top[first:past]
        if any(other is not span and _overlap(other.bbox, span.bbox) for other in near):
            overlapped.add(span.id)
    return overlapped


def _overlap(a: Rect, b: Rect) -> bool:
    """Whether two boxes share more width than a kern takes, over half the shorter's height."""
    across = min(a.x1, b.x1) - max(a.x0, b.x0)
    down = min(a.y1, b.y1) - max(a.y0, b.y0)
    return across > -constants.KERNING_PT and down > min(a.height, b.height) / 2


def _each_baseline(spans: list[Span]) -> Iterator[list[Span]]:
    """The spans on each baseline, top first, each baseline's left to right."""
    by_baseline = sorted(spans, key=lambda span: span.origin[1])
    group: list[Span] = []
    for span in by_baseline:
        # Further below the first on its baseline than one baseline's letters sit apart.
        if group and span.origin[1] - group[0].origin[1] > constants.BASELINE_EPS:
            yield sorted(group, key=lambda placed: placed.bbox.x0)
            group = []
        group.append(span)
    if group:
        yield sorted(group, key=lambda placed: placed.bbox.x0)


def _chains(baseline: list[Span]) -> Iterator[list[Span]]:
    """The runs of a baseline's spans, left to right, each no more than a word space apart."""
    chain = baseline[:1]
    for previous, span in zip(baseline, baseline[1:], strict=False):
        if not _reads_on(previous, span):
            yield chain
            chain = []
        chain.append(span)
    if chain:
        yield chain


def _reads_on(previous: Span, span: Span) -> bool:
    """Whether `span` carries on the line `previous` ends: a kern or a word space after it."""
    gap = span.bbox.x0 - previous.bbox.x1
    word_space = max(previous.size, span.size) * constants.GAP_RATIO
    return constants.KERNING_PT <= gap <= word_space


def _cut(chain: list[Span], *, breaks: list[Rect]) -> list[list[Span]]:
    """`chain` cut wherever a drawing or link level with it has an edge inside it."""
    band = Rect(
        chain[0].bbox.x0,
        min(span.bbox.y0 for span in chain),
        chain[-1].bbox.x1,
        max(span.bbox.y1 for span in chain),
    )
    edges = [edge for box in breaks if _level(box, band) for edge in (box.x0, box.x1)]
    cut_before = {place for edge in edges for place in _boundaries_at(chain, edge)}
    pieces: list[list[Span]] = []
    for place, span in enumerate(chain):
        if place == 0 or place in cut_before:
            pieces.append([])
        pieces[-1].append(span)
    return pieces


def _boundaries_at(chain: list[Span], edge: float) -> Iterator[int]:
    """Each place in `chain` an edge cuts before: inside the span either side, or between."""
    slack = constants.LINE_EDGE_SLACK_PT
    for place in range(1, len(chain)):
        if chain[place - 1].bbox.x0 + slack < edge < chain[place].bbox.x1 - slack:
            yield place


def _level(box: Rect, band: Rect) -> bool:
    """Whether a box's height overlaps a line's at all: a hairline's worth is no overlap."""
    return min(box.y1, band.y1) - max(box.y0, band.y0) > constants.ROOM_TOUCH_PT
