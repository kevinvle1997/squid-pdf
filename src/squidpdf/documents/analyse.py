"""Pool work for documents: what the browser needs before the first edit.

Framework-free and handed only paths, so the pool can pickle it and a test can
call it directly. The shapes are what the Document sends.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import orjson

from squidpdf.core import BUILD, MuPDFEngine
from squidpdf.documents import store


class Box(TypedDict):
    """A box on the page in points, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float


class PageInfo(TypedDict):
    """A page unrotated, and the turn the browser gives it."""

    width: float
    height: float
    rotation: int


class SpanInfo(TypedDict):
    """One editable span and whether it keeps its own font."""

    id: str
    page: int
    text: str
    font: str
    size: float
    color: list[float]
    bbox: Box
    origin: list[float]
    fidelity: str


class FontInfo(TypedDict):
    """A font the spans use: what stands in for it, and every glyph it really draws."""

    name: str
    substitute: str | None
    glyphs: dict[str, float]


class Analysis(TypedDict):
    """Everything worked out from the original under one build."""

    build: str
    pages: list[PageInfo]
    spans: list[SpanInfo]
    fonts: list[FontInfo]


def analyse(folder: str) -> Analysis:
    """Judge every span and list each font's glyphs, under this build, and keep it.

    The index is built on the first run and reused after, so a new build judges
    the same spans and every id holds.
    """
    path = Path(folder)
    with MuPDFEngine(str(path / store.ORIGINAL)) as eng:
        index = store.load_index(path)
        if index is None:
            index = eng.index()
            store.save_index(path, index)
            store.save_pages(path, eng.pages())
        reports = {r.span_id: r for r in eng.assess(index)}
        fonts: dict[str, FontInfo] = {}
        for span in index:
            if span.font not in fonts:
                substitute = reports[span.id].substitute
                fonts[span.font] = {
                    "name": span.font,
                    "substitute": substitute,
                    "glyphs": eng.glyphs(span),
                }

    analysis: Analysis = {
        "build": BUILD,
        "pages": [
            {"width": p.width, "height": p.height, "rotation": p.rotation}
            for p in store.load_pages(path)
        ],
        "spans": [
            {
                "id": s.id,
                "page": s.page,
                "text": s.text,
                "font": s.font,
                "size": s.size,
                "color": list(s.color),
                "bbox": {"x0": s.bbox.x0, "y0": s.bbox.y0, "x1": s.bbox.x1, "y1": s.bbox.y1},
                "origin": list(s.origin),
                "fidelity": reports[s.id].state.value,
            }
            for s in index
        ],
        "fonts": list(fonts.values()),
    }
    store.save_analysis(path, BUILD, orjson.dumps(analysis))
    return analysis


def page_image(folder: str, page: int, scale: float) -> bytes:
    """One page of the original as a PNG, unrotated."""
    with MuPDFEngine(str(Path(folder) / store.ORIGINAL)) as eng:
        return eng.page_image(page, scale)
