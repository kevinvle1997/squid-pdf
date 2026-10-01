"""Work the worker pool runs for documents: what the browser needs before the first edit.

Framework-free and handed only paths and numbers, so the pool can pickle it and
a test can call it directly.
"""

from __future__ import annotations

from pathlib import Path

import orjson

from squidpdf.core import BUILD, Fidelity, FidelityReport, MessageInfo, Span
from squidpdf.documents import store
from squidpdf.documents.errors import TooManyPages
from squidpdf.documents.types import Analysis, FontFacts, SpanInfo

__all__ = [
    "analyse",
]


def analyse(folder: str, max_pages: int) -> Analysis:
    """Judge every span and list each font's letters, under this build, and keep it.

    Kept in no language: each sentence as its code and facts, said when it's sent.

    The index is built on the first run and reused after, so a new build judges
    the same spans and every id holds. Raises TooManyPages first, if it has
    more than `max_pages`.
    """
    path = Path(folder)
    with store.open_to_analyse(path) as engine:
        index = store.load_index(path)
        if index is None:
            if engine.page_count() > max_pages:
                raise TooManyPages(max_pages)
            index = engine.index()
            store.save_index(path, index)
            store.save_pages(path, engine.pages())
        reports = {report.span_id: report for report in engine.assess(index)}
        # Index order: the first span in each font speaks for it.
        first_span_of_font: dict[str, Span] = {}
        for span in index:
            first_span_of_font.setdefault(span.font, span)
        fonts: list[FontFacts] = [
            {
                "name": span.font,
                "substitute": reports[span.id].substitute,
                "why": why_of(reports[span.id], Fidelity.SUBSTITUTE),
                "same_widths": reports[span.id].same_widths,
                "glyphs": engine.widths(span),
            }
            for span in first_span_of_font.values()
        ]

    analysis: Analysis = {
        "build": BUILD,
        "pages": [
            {"width": page.width, "height": page.height, "rotation": page.rotation}
            for page in store.load_pages(path)
        ],
        "spans": [span_info(span, reports[span.id]) for span in index],
        "fonts": fonts,
    }
    store.save_analysis(path, BUILD, orjson.dumps(analysis))
    return analysis


def why_of(report: FidelityReport, state: Fidelity) -> MessageInfo | None:
    """Why the span is `state`, in no language yet; None when it's something else.

    A font's `why` is why a substitute stands in; a span's is how an approximate
    one would come back unlike itself.
    """
    if report.state is not state or report.why is None:
        return None
    return report.why.as_info()


def span_info(span: Span, report: FidelityReport) -> SpanInfo:
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
        "why": why_of(report, Fidelity.APPROXIMATE),
    }
