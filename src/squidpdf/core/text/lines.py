"""Edited apart, each span has almost no room: a span's room ends where the next starts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator

import squidpdf.core.constants as constants
from squidpdf.core.types import Rect, Span


def lines_on(spans: list[Span], *, breaks: list[Rect], in_fields: set[str]) -> list[list[Span]]:
    """A drawing or link with an edge inside a line cuts it: a moved span would leave it."""
    level = [span for span in spans if not span.turned]
    apart = in_fields | _overlapped(level)
    joinable = [span for span in level if span.id not in apart]
    chains = [chain for baseline in _each_baseline(joinable) for chain in _chains(baseline)]
    lines = [cut for chain in chains if len(chain) > 1 for cut in _cut(chain, breaks=breaks)]
    return [line for line in lines if len(line) > 1]


def _overlapped(spans: list[Span]) -> set[str]:
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
    across = min(a.x1, b.x1) - max(a.x0, b.x0)
    down = min(a.y1, b.y1) - max(a.y0, b.y0)
    return across > -constants.KERNING_PT and down > min(a.height, b.height) / 2


def _each_baseline(spans: list[Span]) -> Iterator[list[Span]]:
    by_baseline = sorted(spans, key=lambda span: span.origin[1])
    group: list[Span] = []
    for span in by_baseline:
        if group and span.origin[1] - group[0].origin[1] > constants.BASELINE_EPS:
            yield sorted(group, key=lambda placed: placed.bbox.x0)
            group = []
        group.append(span)
    if group:
        yield sorted(group, key=lambda placed: placed.bbox.x0)


def _chains(baseline: list[Span]) -> Iterator[list[Span]]:
    chain = baseline[:1]
    for previous, span in zip(baseline, baseline[1:], strict=False):
        if not _reads_on(previous, span):
            yield chain
            chain = []
        chain.append(span)
    if chain:
        yield chain


def _reads_on(previous: Span, span: Span) -> bool:
    gap = span.bbox.x0 - previous.bbox.x1
    word_space = max(previous.size, span.size) * constants.GAP_RATIO
    return constants.KERNING_PT <= gap <= word_space


def _cut(chain: list[Span], *, breaks: list[Rect]) -> list[list[Span]]:
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
    slack = constants.LINE_EDGE_SLACK_PT
    for place in range(1, len(chain)):
        if chain[place - 1].bbox.x0 + slack < edge < chain[place].bbox.x1 - slack:
            yield place


def _level(box: Rect, band: Rect) -> bool:
    return min(box.y1, band.y1) - max(box.y0, band.y0) > constants.ROOM_TOUCH_PT
