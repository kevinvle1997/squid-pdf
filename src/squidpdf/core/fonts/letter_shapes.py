"""A trimmed TrueType often keeps no OS/2 table, and its PDF says nothing of its kind either."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fontTools.pens.basePen import decomposeQuadraticSegment, decomposeSuperBezierSegment
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

import squidpdf.core.constants as constants
from squidpdf.core.types import Category, GlyphId, GlyphName

# Letters whose lower part is upright stems alone in a sans, so a foot there is a serif.
_STEM_LETTERS = "HITFPhilmnr"
# Where a stem is measured, as shares of the letter's height from its lowest point: through
# its foot, and a quarter up, where every one of these letters is stems and nothing else.
_FOOT_HEIGHT = 0.03
_STEM_HEIGHT = 0.25
# Straight edges a curve is cut into: plenty to measure a foot a few % of a letter tall.
_CURVE_STEPS = 8

type _Point = tuple[float, float]
type _Edge = tuple[_Point, _Point]


def kind_by_shape(font: TTFont, letters: Mapping[str, GlyphId]) -> Category | None:
    """`letters` gives each letter's glyph from the page's letter list, where the cmap can't."""
    glyph_set = font.getGlyphSet()
    named = _stem_glyphs(font, letters)
    outlines = [_edges_of(glyph_set, name) for name in named]
    measured = [edges for edges in outlines if edges]
    if len(measured) < constants.SERIF_LETTERS_MIN:
        return None
    advances = {font["hmtx"][name][0] for name in named}
    if len(named) >= constants.MONO_LETTERS_MIN and len(advances) == 1:
        return "mono"
    footed = sum(_has_foot(edges) for edges in measured)
    share = footed / len(measured)
    serif = footed >= constants.SERIF_LETTERS_MIN and share >= constants.SERIF_SHARE
    return "serif" if serif else "sans"


def _stem_glyphs(font: TTFont, letters: Mapping[str, GlyphId]) -> list[GlyphName]:
    order = font.getGlyphOrder()
    # A trimmed copy may keep no letter table (cmap), or one for no Unicode letter.
    cmap = (font.getBestCmap() if "cmap" in font else None) or {}
    names: list[GlyphName] = []
    for letter in _STEM_LETTERS:
        glyph = letters.get(letter)  # a trimmed font draws only the letters its page used
        name = (
            order[glyph]
            if glyph is not None and 0 < glyph < len(order)
            else cmap.get(ord(letter))
        )
        if name is not None:
            names.append(name)
    return names


def _edges_of(glyph_set: Mapping[GlyphName, Any], name: GlyphName) -> list[_Edge]:
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[name].draw(pen)
    edges: list[_Edge] = []
    start = current = (0.0, 0.0)
    for command, points in pen.value:
        if command == "moveTo":
            start = current = points[0]
            continue
        if command in ("closePath", "endPath"):
            edges.append((current, start))
            current = start
            continue
        if command == "lineTo":
            edges.append((current, points[0]))
            current = points[0]
            continue
        for segment in _curves_of(command, points):
            edges.extend(_flattened((current, *segment)))
            current = segment[-1]
    return edges


def _curves_of(command: str, points: tuple[_Point, ...]) -> list[tuple[_Point, ...]]:
    if command == "qCurveTo":
        # TrueType's run of off-curve points, an on-curve point implied between each two.
        return decomposeQuadraticSegment(points)
    return decomposeSuperBezierSegment(points)


def _flattened(controls: tuple[_Point, ...]) -> list[_Edge]:
    along = [_point_on(controls, step / _CURVE_STEPS) for step in range(1, _CURVE_STEPS + 1)]
    points = [controls[0], *along]
    return list(zip(points, points[1:], strict=False))


def _has_foot(edges: list[_Edge]) -> bool:
    heights = [y for edge in edges for _x, y in edge]
    lowest, height = min(heights), max(heights) - min(heights)
    stem = _width_at(edges, lowest + _STEM_HEIGHT * height)
    foot = _width_at(edges, lowest + _FOOT_HEIGHT * height)
    return stem > 0 and foot >= constants.SERIF_FOOT_SPREAD * stem


def _width_at(edges: list[_Edge], y: float) -> float:
    crossings = sorted(
        start[0] + (y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
        for start, end in edges
        if min(start[1], end[1]) <= y < max(start[1], end[1])
    )
    return sum(
        right - left for left, right in zip(crossings[::2], crossings[1::2], strict=False)
    )


def _point_on(controls: tuple[_Point, ...], t: float) -> _Point:
    """De Casteljau's: the control polygon cut at `t` until one point is left."""
    while len(controls) > 1:
        controls = tuple(
            ((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1])
            for a, b in zip(controls, controls[1:], strict=False)
        )
    return controls[0]
