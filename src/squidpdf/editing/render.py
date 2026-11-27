"""No web framework here, so a worker can import it."""

from __future__ import annotations

import asyncio
import base64
import math
from dataclasses import dataclass
from functools import partial
from itertools import pairwise
from pathlib import Path

import orjson

from squidpdf.core import (
    BUILD,
    Engine,
    InvalidRequest,
    Page,
    Rect,
    Reply,
    Workers,
    words,
)
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage, is_page
from squidpdf.documents.free_space import kept_free_space
from squidpdf.documents.page_image import page_scale
from squidpdf.documents.replies import time_of
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import Loaded
from squidpdf.editing import constants
from squidpdf.editing.apply import (
    edited_by,
    fits_of,
    plan,
    redacted_by,
    resolve,
    run,
    verdicts_of,
)
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.redaction import redactions_of
from squidpdf.editing.replies import (
    fit_info,
    notice_info,
    redaction_info,
    rest_of_line_said,
    skipped_info,
    why_unredacted,
)
from squidpdf.editing.types import (
    DrawnPage,
    ImageInfo,
    Notice,
    Redaction,
    Region,
    Render,
    Rendered,
    SpanNotice,
)


@dataclass(frozen=True, slots=True, eq=False)
class RenderController:
    workers: Workers
    documents: DocumentStore

    async def render(
        self,
        doc: Loaded,
        *,
        edits: list[Edit],
        regions: list[Region],
        scale: int,
        language: str,
    ) -> Reply[bytes]:
        check_edits(edits)
        _check_region_count(regions)
        pages = await asyncio.to_thread(store.load_pages, doc.folder)
        strips = _checked_strips(regions, pages)
        # Only the pages drawn go to the worker, each at its image's scale, so strips line up.
        drawn_pages = {
            number: DrawnPage(pages[number], page_scale(pages[number], scale))
            for number in strips
        }
        _check_rows(strips, drawn_pages)
        rendered = await self._enqueue_draw_regions(
            doc.folder, edits=edits, regions=regions, strips=strips, drawn_pages=drawn_pages
        )
        body = await asyncio.to_thread(
            _reply_json, rendered, expires_at=doc.expires_at, language=language
        )
        return Reply(body, words.language_headers(language))

    async def _enqueue_draw_regions(
        self,
        folder: Path,
        *,
        edits: list[Edit],
        regions: list[Region],
        strips: dict[int, list[Rect]],
        drawn_pages: dict[int, DrawnPage],
    ) -> Rendered:
        task = partial(
            _draw_regions,
            self.documents,
            folder,
            edits=edits,
            regions=regions,
            strips=strips,
            drawn_pages=drawn_pages,
        )
        return await self.workers.run(constants.RENDER_TIMEOUT_S, task)


def _draw_regions(
    documents: DocumentStore,
    folder: Path,
    *,
    edits: list[Edit],
    regions: list[Region],
    strips: dict[int, list[Rect]],
    drawn_pages: dict[int, DrawnPage],
) -> Rendered:
    index = store.require_index(folder)
    free_space = kept_free_space(documents, folder, index)
    with documents.open_original(folder) as engine:
        resolved = resolve(engine, index, free_space=free_space, edits=edits)
        fits = fits_of(engine, resolved)
        steps = plan(engine, resolved, fits, strips=strips)
        redactions = redactions_of(
            engine, resolved, redacted_by(steps), edited=edited_by(steps)
        )
        ran = run(engine, steps)
        verdicts = verdicts_of(fits, ran)
        redaction_per_span = redactions.drawn(engine)
        images = _draw(engine, regions, drawn_pages=drawn_pages)

    return Rendered(
        images,
        fits={span_id: fitted.report for span_id, fitted in fits.replaces.items()}
        | {run.key: run.report for fitted in fits.lines.values() for run in fitted.redrawn},
        insert_fits={position: fitted.report for position, fitted in fits.inserts.items()},
        verdicts=verdicts.replaces,
        insert_verdicts=verdicts.inserts,
        skipped=resolved.skipped,
        notices=ran.notices + _said_of_redactions(redaction_per_span),
        redactions=redaction_per_span,
    )


def _said_of_redactions(redactions: dict[str, Redaction]) -> list[Notice]:
    """Said now, while the user can still undo it: export refuses the first kind."""
    unredacted: list[Notice] = [
        SpanNotice(span_id, why_unredacted(redaction))
        for span_id, redaction in redactions.items()
        if not redaction.verified
    ]
    line_changed = [
        SpanNotice(span_id, said)
        for span_id, redaction in redactions.items()
        if redaction.verified
        for said in rest_of_line_said(redaction)
    ]
    return unredacted + line_changed


def _check_region_count(regions: list[Region]) -> None:
    if len(regions) > constants.MAX_REGIONS:
        raise InvalidRequest(debug=f"regions: {len(regions)}, at most {constants.MAX_REGIONS}")


def _checked_strips(regions: list[Region], pages: list[Page]) -> dict[int, list[Rect]]:
    strips: dict[int, list[Rect]] = {}
    for region in regions:
        if not is_page(region.page, len(pages)):
            raise NoSuchPage(debug=f"regions: no page {region.page}")
        page = pages[region.page]
        strip = _strip_of(region, page)
        # `not <` rather than `>=`: every comparison with NaN is false, so NaN fails too.
        if not strip.y0 < strip.y1:
            debug = f"regions: y0 above y1, and on page {region.page}'s 0 to {page.height:g}"
            raise InvalidRequest(debug=debug)
        strips.setdefault(region.page, []).append(strip)
    if len(strips) > constants.MAX_RENDER_PAGES:
        debug = f"regions: on {len(strips)} pages, at most {constants.MAX_RENDER_PAGES}"
        raise InvalidRequest(debug=debug)
    for page_number, page_strips in strips.items():
        _check_apart(page_number, page_strips)
    return strips


def _check_apart(page_number: int, strips: list[Rect]) -> None:
    ordered = sorted(strips, key=lambda strip: strip.y0)
    for above, below in pairwise(ordered):
        if below.y0 < above.y1:
            debug = f"regions: two on page {page_number} share the rows from {below.y0:g}"
            raise InvalidRequest(debug=debug)


def _check_rows(strips: dict[int, list[Rect]], drawn_pages: dict[int, DrawnPage]) -> None:
    """Each is drawn out to whole rows, so thin slivers would add up to many times the page."""
    for number, page_strips in strips.items():
        page, scale = drawn_pages[number].page, drawn_pages[number].scale
        rows = sum(len(_pixel_rows(strip, scale)) for strip in page_strips)
        page_rows = len(_pixel_rows(Rect(0.0, 0.0, page.width, page.height), scale))
        most = constants.MAX_STRIP_ROWS_PER_PAGE_ROW * page_rows
        if rows > most:
            debug = f"regions: {rows} pixel rows on page {number}, at most {most}"
            raise InvalidRequest(debug=debug)


def _strip_of(region: Region, page: Page) -> Rect:
    top = 0.0 if region.y0 is None else max(region.y0, 0.0)
    bottom = page.height if region.y1 is None else min(region.y1, page.height)
    return Rect(0.0, top, page.width, bottom)


def _reply_json(rendered: Rendered, *, expires_at: float, language: str) -> bytes:
    return orjson.dumps(_reply_body(rendered, expires_at=expires_at, language=language))


def _reply_body(rendered: Rendered, *, expires_at: float, language: str) -> Render:
    return {
        "images": rendered.images,
        "fits": {
            span_id: fit_info(fit, rendered.verdicts[span_id], language)
            for span_id, fit in rendered.fits.items()
        },
        "insert_fits": [
            {**fit_info(fit, rendered.insert_verdicts[position], language), "edit": position}
            for position, fit in rendered.insert_fits.items()
        ],
        "redactions": {
            span_id: redaction_info(redaction, language)
            for span_id, redaction in rendered.redactions.items()
        },
        "skipped": [skipped_info(skipped, language) for skipped in rendered.skipped],
        "notices": [notice_info(notice, language) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": time_of(expires_at),
    }


def _draw(
    engine: Engine, regions: list[Region], *, drawn_pages: dict[int, DrawnPage]
) -> list[ImageInfo]:
    on_page: dict[int, list[Region]] = {}
    for region in regions:
        on_page.setdefault(region.page, []).append(region)
    drawn = {
        number: iter(_draw_page(engine, page_regions, drawn_page=drawn_pages[number]))
        for number, page_regions in on_page.items()
    }
    return [next(drawn[region.page]) for region in regions]


def _draw_page(
    engine: Engine, regions: list[Region], *, drawn_page: DrawnPage
) -> list[ImageInfo]:
    page, scale = drawn_page.page, drawn_page.scale
    number = regions[0].page
    # No two regions of a page share rows (_check_apart), so a whole page is its only one.
    whole_page = regions[0].y0 is None and regions[0].y1 is None
    if whole_page:
        return [_image_of(number, 0.0, engine.page_image(number, scale))]
    boxes = [_whole_rows(_strip_of(region, page), scale) for region in regions]
    pngs = engine.box_images(number, scale, boxes)
    return [_image_of(number, box.y0, png) for box, png in zip(boxes, pngs, strict=True)]


def _whole_rows(strip: Rect, scale: float) -> Rect:
    rows = _pixel_rows(strip, scale)
    return Rect(strip.x0, rows.start / scale, strip.x1, rows.stop / scale)


def _pixel_rows(strip: Rect, scale: float) -> range:
    return range(math.floor(strip.y0 * scale), math.ceil(strip.y1 * scale))


def _image_of(page: int, y: float, png: bytes) -> ImageInfo:
    return {"page": page, "y": y, "image": base64.b64encode(png).decode()}
