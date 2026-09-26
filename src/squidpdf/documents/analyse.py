"""Pool work for documents: what the browser needs before the first edit.

Framework-free and handed only paths, so the pool can pickle it and a test can
call it directly.
"""

from __future__ import annotations

from pathlib import Path

import orjson

from squidpdf.api import constants as limits
from squidpdf.core import BUILD, FidelityReport, MuPDFEngine, Span, new_text
from squidpdf.core.fonts import BUILT_IN
from squidpdf.documents import store
from squidpdf.documents.types import Analysis, FontInfo, InsertFontInfo, SpanInfo


class TooManyPages(Exception):
    """The PDF has more pages than the server works on; checked before any other work."""


def analyse(folder: str) -> Analysis:
    """Judge every span and list each font's letters, under this build, and keep it.

    The index is built on the first run and reused after, so a new build judges
    the same spans and every id holds. Raises TooManyPages first, if it has.
    """
    path = Path(folder)
    with MuPDFEngine(str(path / store.ORIGINAL)) as eng:
        index = store.load_index(path)
        if index is None:
            if len(eng.pages()) > limits.MAX_PAGES:
                raise TooManyPages
            index = eng.index()
            store.save_index(path, index)
            store.save_pages(path, eng.pages())
        reports = {report.span_id: report for report in eng.assess(index)}
        # Index order: the first span in each font speaks for it.
        first_span_of_font: dict[str, Span] = {}
        for span in index:
            first_span_of_font.setdefault(span.font, span)
        # Each built-in face's letters, measured as a first-page insert would be.
        insert_fonts: list[InsertFontInfo] = [
            {"name": name, "glyphs": eng.glyphs(new_text(0, (0.0, 0.0), "", 1.0, name))}
            for name in BUILT_IN
        ]
        fonts: list[FontInfo] = [
            {
                "name": span.font,
                "substitute": reports[span.id].substitute,
                "why": reports[span.id].why,
                "glyphs": eng.glyphs(span),
            }
            for span in first_span_of_font.values()
        ]

    analysis: Analysis = {
        "build": BUILD,
        "pages": [
            {"width": page.width, "height": page.height, "rotation": page.rotation}
            for page in store.load_pages(path)
        ],
        "spans": [_span_info(span, reports[span.id]) for span in index],
        "fonts": fonts,
        "insert_fonts": insert_fonts,
    }
    store.save_analysis(path, BUILD, orjson.dumps(analysis))
    return analysis


def page_image(folder: str, page: int, scale: float) -> bytes:
    """One page of the original as a PNG, unrotated."""
    with MuPDFEngine(str(Path(folder) / store.ORIGINAL)) as eng:
        return eng.page_image(page, scale)


def _span_info(span: Span, report: FidelityReport) -> SpanInfo:
    """One span as the browser gets it, with how well it keeps its own font."""
    box = span.bbox
    return {
        "id": span.id,
        "page": span.page,
        "text": span.text,
        "font": span.font,
        "size": span.size,
        "color": list(span.color),
        "bbox": {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1},
        "origin": list(span.origin),
        "fidelity": report.state.value,
    }
