"""Work the worker pool runs for documents: what the browser needs before the first edit.

Framework-free and handed only paths and numbers, so the pool can pickle it and
a test can call it directly.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import xxhash

from squidpdf.core import (
    BUILD,
    Engine,
    FidelityReport,
    MessageInfo,
    Span,
    SpanIndex,
    reason_of,
    strip_subset,
)
from squidpdf.documents import store
from squidpdf.documents.errors import TooManyPages
from squidpdf.documents.types import (
    Analysis,
    AnalysisFacts,
    ApproximateInfo,
    FontFacts,
    KeptAnalysis,
    SpanInfo,
)


def analyse_upload(folder: str, max_pages: int) -> KeptAnalysis:
    """Index a new upload, keep its pages, and analyse it.

    The one place an index is built, so no span id the browser holds ever moves.
    Raises TooManyPages past `max_pages`, before reading a page.
    """
    path = Path(folder)
    attached = store.attached_files(path)
    with store.open_to_analyse(path, attached) as engine:
        if engine.page_count() > max_pages:
            raise TooManyPages(max_pages)
        index = engine.index()
        store.save_index(path, index)
        store.save_pages(path, engine.pages())
        return _analysed(engine, path, index, attached=attached)


def analyse(folder: str) -> KeptAnalysis:
    """Analyse a stored document again, under this build, over the index kept at upload.

    Raises Gone if that index was deleted or kept in another format.
    """
    path = Path(folder)
    index = store.require_index(path)
    attached = store.attached_files(path)
    with store.open_to_analyse(path, attached) as engine:
        return _analysed(engine, path, index, attached=attached)


def _analysed(
    engine: Engine, path: Path, index: SpanIndex, *, attached: store.AttachedFiles
) -> KeptAnalysis:
    """Judge every span and list each font's letters, under this build; keep and return it.

    Kept under the set of the user's copies it was judged with, `attached`.

    Kept in no language: each sentence as its code and facts, said when it's sent.
    Handed back as its JSON, so it leaves the worker fast and the reply sends its spans as is.
    """
    reports = {report.span_id: report for report in engine.assess(index)}
    # Text a form field draws: said before any edit, since an edit to it is left out.
    form_field_span_ids = {span.id for span in engine.in_form_fields(index)}
    # How far each line can run before it's too long: the browser's fit check needs it.
    rooms = engine.rooms(index, index)
    # Index order: the first span in each font speaks for it.
    first_span_of_font: dict[str, Span] = {}
    for span in index:
        first_span_of_font.setdefault(span.font, span)
    fonts: list[FontFacts] = [
        {
            "name": span.font,
            "substitute": reports[span.id].substitute,
            "why": _substitute_why(reports[span.id]),
            "same_widths": reports[span.id].same_widths,
            "attached": strip_subset(span.font) in attached,
            "glyphs": engine.widths(span),
        }
        for span in first_span_of_font.values()
    ]
    analysis: Analysis = {
        "build": BUILD,
        "pages": [
            {"width": page.width, "height": page.height, "turn_cw": page.turn_cw}
            for page in store.load_pages(path)
        ],
        "spans": [
            _span_info(
                span,
                reports[span.id],
                room_pt=rooms[span.id],
                form_field=span.id in form_field_span_ids,
            )
            for span in index
        ],
        "fonts": fonts,
    }
    kept = _kept_analysis(analysis)
    store.save_analysis(path, BUILD, kept, attached=attached.key)
    return kept


def _kept_analysis(analysis: Analysis) -> KeptAnalysis:
    """The analysis as kept and sent: its spans apart from the rest, and a digest of both."""
    facts: AnalysisFacts = {
        "build": analysis["build"],
        "pages": analysis["pages"],
        "fonts": analysis["fonts"],
    }
    facts_json, spans_json = orjson.dumps(facts), orjson.dumps(analysis["spans"])
    digest = xxhash.xxh3_64_hexdigest(facts_json + spans_json)
    return KeptAnalysis(facts_json, spans_json, digest)


def _substitute_why(report: FidelityReport) -> MessageInfo | None:
    """Why a substitute stands in for the span's font, in no language yet; None if none does."""
    if report.state != "substitute" or report.why is None:
        return None
    return report.why.as_info()


def _approximate_why(report: FidelityReport) -> ApproximateInfo | None:
    """How an approximate span would come back unlike itself, in no language.

    None when it isn't approximate.
    """
    if report.state != "approximate" or report.why is None:
        return None
    return {"code": reason_of(report.why), "params": report.why.params}


def _span_info(
    span: Span, report: FidelityReport, *, room_pt: float, form_field: bool
) -> SpanInfo:
    """One span as the browser gets it, with how well it keeps its own font.

    `room_pt` is how far its line can run (`Engine.rooms`); `form_field` when a
    form field draws it, not the page.
    """
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
        "fidelity": report.state,
        "why": _approximate_why(report),
        "room_pt": round(room_pt, 2),
        "form_field": form_field,
    }
