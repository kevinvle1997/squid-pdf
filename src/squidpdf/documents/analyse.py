from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path

import orjson
import xxhash

from squidpdf.core import (
    BUILD,
    Engine,
    FidelityReport,
    FreeSpace,
    Line,
    MessageInfo,
    Span,
    SpanIndex,
    index_of,
    reason_of,
    strip_subset,
)
from squidpdf.documents import attached, store
from squidpdf.documents.errors import TooManyPages
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import (
    AnalysisFacts,
    ApproximateInfo,
    FontFacts,
    JudgedPages,
    KeptAnalysis,
    LineInfo,
    SpanInfo,
)

_SPACING_DP = 4  # letter spacing, per em: finer than any page sets it
_ROOM_DP = 2  # a line's room, in points: a hundredth is finer than any line is set
_EMPTY_ARRAY = b"[]"


def analyse_upload(
    documents: DocumentStore,
    folder: Path,
    max_pages: int,
    *,
    most_shares: int,
    spans_per_share: int,
) -> KeptAnalysis | list[range]:
    """The one place an index is built, so no span id the browser holds ever moves."""
    _take_in(documents, folder, max_pages)
    files = attached.attached_files(folder)
    with documents.open_to_analyse(folder, files) as engine:
        index = engine.index()
        store.save_index(folder, index)
        store.save_pages(folder, engine.pages())
        spans_on = [0] * engine.page_count()
        for span in index:
            spans_on[span.page] += 1
        shares = _shares_of(spans_on, most=most_shares, spans_each=spans_per_share)
        if len(shares) > 1:
            return shares
        every_page = _judged(engine, folder, index, pages=shares[0], files=files)
    return keep_judged(folder, [every_page])


def _shares_of(spans_on: list[int], *, most: int, spans_each: int) -> list[range]:
    total = sum(spans_on)
    count = max(min(most, total // spans_each), 1)
    cuts: list[int] = []
    so_far = in_share = 0
    for page, spans in enumerate(spans_on):
        so_far += spans
        in_share += spans
        reached_its_part = so_far * count >= total * (len(cuts) + 1)
        ends_here = len(cuts) < count - 1 and in_share > 0 and reached_its_part
        if ends_here and so_far < total:
            cuts.append(page + 1)
            in_share = 0
    bounds = [0, *cuts, len(spans_on)]
    return [range(start, stop) for start, stop in pairwise(bounds)]


def _take_in(documents: DocumentStore, folder: Path, max_pages: int) -> None:
    """Before the index, so every span id is the rewritten file's."""
    with documents.open_original(folder) as engine:
        if engine.page_count() > max_pages:
            raise TooManyPages(max_pages)
        active_content_dropped = engine.drop_active_content()
        plain_xmp_dropped = engine.drop_plain_xmp()
        rewritten = active_content_dropped or plain_xmp_dropped
        if not rewritten:
            return
        store.replace_original(folder, engine.save)
    if active_content_dropped:
        store.keep_active_content_dropped(folder)
    if plain_xmp_dropped:
        store.keep_plain_xmp_dropped(folder)


def analyse(documents: DocumentStore, folder: Path) -> KeptAnalysis:
    every_page = range(len(store.load_pages(folder)))
    return keep_judged(folder, [judge_pages(documents, folder, every_page)])


def judge_pages(documents: DocumentStore, folder: Path, pages: range) -> JudgedPages:
    index = store.require_index(folder)
    files = attached.attached_files(folder)
    with documents.open_to_analyse(folder, files) as engine:
        return _judged(engine, folder, index, pages=pages, files=files)


def _judged(
    engine: Engine,
    folder: Path,
    index: SpanIndex,
    *,
    pages: range,
    files: attached.AttachedFiles,
) -> JudgedPages:
    on_pages = index_of(span for span in index if span.page in pages)
    kept = store.load_free_space(folder, BUILD)
    free_space = engine.free_space(on_pages) if kept is None else kept
    reports = {report.span_id: report for report in engine.assess(on_pages)}
    form_field_span_ids = {span.id for span in engine.in_form_fields(on_pages)}
    spans = [
        _span_info(
            span,
            reports[span.id],
            room_pt=free_space.rooms[span.id],
            form_field=span.id in form_field_span_ids,
        )
        for span in on_pages
    ]
    return JudgedPages(
        orjson.dumps(spans),
        fonts=_fonts_of(engine, on_pages, reports=reports, files=files),
        lines=[
            _line_info(line) for line in free_space.lines if line.span_ids[0] in on_pages.by_id
        ],
        free_space=free_space if kept is None else None,
        signed=engine.holds_signature(),
        attached=files.key,
    )


def keep_judged(folder: Path, judged: list[JudgedPages]) -> KeptAnalysis:
    worked_out = [share.free_space for share in judged if share.free_space is not None]
    if worked_out:
        store.save_free_space(folder, BUILD, _joined_free_space(worked_out))
    fonts: dict[tuple[int | None, str], FontFacts] = {}
    for share in judged:
        for font in share.fonts:
            fonts.setdefault((font["font_object"], font["name"]), font)
    facts = _document_facts(
        folder,
        signed=judged[0].signed,
        fonts=list(fonts.values()),
        lines=[line for share in judged for line in share.lines],
    )
    kept = _kept_analysis(facts, _joined([share.spans for share in judged]))
    store.save_analysis(folder, BUILD, kept, attached=judged[0].attached)
    return kept


def _joined_free_space(shares: list[FreeSpace]) -> FreeSpace:
    rooms = {span_id: room for share in shares for span_id, room in share.rooms.items()}
    return FreeSpace(rooms, [line for share in shares for line in share.lines])


def _joined(arrays: list[bytes]) -> bytes:
    items = [array[1:-1] for array in arrays if array != _EMPTY_ARRAY]
    return b"[" + b",".join(items) + b"]"


def _fonts_of(
    engine: Engine,
    index: SpanIndex,
    *,
    reports: Mapping[str, FidelityReport],
    files: attached.AttachedFiles,
) -> list[FontFacts]:
    first_span_of_font: dict[tuple[int | None, str], Span] = {}
    for span in index:
        first_span_of_font.setdefault((span.font_object, span.font), span)
    return [
        {
            "font_object": span.font_object,
            "name": span.font,
            "shown_name": engine.shown_name(span),
            "substitute": reports[span.id].substitute,
            "why": _substitute_why(reports[span.id]),
            "same_widths": reports[span.id].same_widths,
            "look_alike": engine.look_alike(span),
            "attached": strip_subset(span.font) in files,
            "glyphs": engine.widths(span),
        }
        for span in first_span_of_font.values()
    ]


def _document_facts(
    folder: Path, *, signed: bool, fonts: list[FontFacts], lines: list[LineInfo]
) -> AnalysisFacts:
    return {
        "build": BUILD,
        "active_content_dropped": store.active_content_dropped(folder),
        "plain_xmp_dropped": store.plain_xmp_dropped(folder),
        "signed": signed,
        "pages": [
            {"width": page.width, "height": page.height, "turn_cw": page.turn_cw}
            for page in store.load_pages(folder)
        ],
        "fonts": fonts,
        "lines": lines,
    }


def _line_info(line: Line) -> LineInfo:
    return {"span_ids": list(line.span_ids), "room_pt": round(line.room_pt, _ROOM_DP)}


def _kept_analysis(facts: AnalysisFacts, spans_json: bytes) -> KeptAnalysis:
    facts_json = orjson.dumps(facts)
    digest = xxhash.xxh3_64_hexdigest(facts_json + spans_json)
    return KeptAnalysis(facts_json, spans_json, digest)


def _substitute_why(report: FidelityReport) -> MessageInfo | None:
    if report.state != "substitute" or report.why is None:
        return None
    return report.why.as_info()


def _approximate_why(report: FidelityReport) -> ApproximateInfo | None:
    if report.state != "approximate" or report.why is None:
        return None
    return {"code": reason_of(report.why), "params": report.why.params}


def _span_info(
    span: Span, report: FidelityReport, *, room_pt: float, form_field: bool
) -> SpanInfo:
    box = span.bbox
    return {
        "id": span.id,
        "page": span.page,
        "text": span.text,
        "font": span.font,
        "font_object": span.font_object,
        "size": span.size,
        "color": list(span.color),
        "bbox": {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1},
        "origin": list(span.origin),
        "fidelity": report.state,
        "why": _approximate_why(report),
        "substitute": report.substitute,
        "same_widths": report.same_widths,
        "in_file": report.in_file,
        "letter_spacing_em": round(report.letter_spacing, _SPACING_DP),
        "room_pt": round(room_pt, _ROOM_DP),
        "form_field": form_field,
        "turned": span.turned,
    }
