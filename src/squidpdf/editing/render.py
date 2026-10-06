"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import asyncio
import base64
import math
from collections import Counter
from dataclasses import dataclass
from functools import partial
from itertools import pairwise
from pathlib import Path

from squidpdf.core import (
    BUILD,
    Engine,
    InvalidRequest,
    Message,
    Page,
    Rect,
    Reply,
    Span,
    Workers,
    words,
)
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.page_image import page_scale
from squidpdf.documents.replies import time_of
from squidpdf.documents.types import Loaded
from squidpdf.editing import constants
from squidpdf.editing.apply import Erase, Step, is_page, log_fits, plan, resolve, run
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.replies import fit_info, notice_info, redaction_info, skipped_info
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
    """Render, from request to reply."""

    workers: Workers  # where it draws, off the server's own thread

    async def render(
        self,
        doc: Loaded,
        *,
        edits: list[Edit],
        regions: list[Region],
        scale: int,
        said_in: str,
    ) -> Reply[Render]:
        """Each region drawn with its page's edits, a fit per edit, and what was skipped.

        A redaction pointing at nothing fails the whole request.
        """
        check_edits(edits)
        _check_region_count(regions)
        # Read and parsed from disk: off the server's thread.
        pages = await asyncio.to_thread(store.load_pages, doc.folder)
        _check_regions(regions, pages)
        # Only the pages drawn go to the worker, so it needn't read the page list again,
        # each at its page image's scale, so a strip and the image line up.
        drawn_pages = {
            region.page: DrawnPage(pages[region.page], page_scale(pages[region.page], scale))
            for region in regions
        }
        _check_rows(regions, drawn_pages)
        rendered = await self._enqueue_draw_regions(
            doc.folder, edits=edits, regions=regions, drawn_pages=drawn_pages
        )
        body = _reply_body(rendered, doc.expires_at, said_in)
        return Reply(body, words.language_headers(said_in))

    async def _enqueue_draw_regions(
        self,
        folder: Path,
        *,
        edits: list[Edit],
        regions: list[Region],
        drawn_pages: dict[int, DrawnPage],
    ) -> Rendered:
        """Draw the regions on a worker."""
        task = partial(
            _draw_regions, str(folder), edits=edits, regions=regions, drawn_pages=drawn_pages
        )
        return await self.workers.run(constants.RENDER_TIMEOUT_S, task)


def _draw_regions(
    folder: str,
    *,
    edits: list[Edit],
    regions: list[Region],
    drawn_pages: dict[int, DrawnPage],
) -> Rendered:
    """Apply the edits the regions show, then draw each region. Runs in a worker.

    `drawn_pages` are the pages the regions are on, by number.
    """
    path = Path(folder)
    index = store.require_index(path)
    strips: dict[int, list[Rect]] = {}
    for region in regions:
        strips.setdefault(region.page, []).append(
            _strip_of(region, drawn_pages[region.page].page)
        )

    with store.open_original(path) as engine:
        resolved = resolve(engine, edits, index)
        fits = log_fits(engine, resolved)  # before run: erasing can drop the fonts it measures
        steps = plan(engine, resolved, fits, strips=strips)
        redactions = RedactionController(tuple(_redacted_by(steps)))
        hidden = redactions.hidden_places(engine)  # before run: it takes the words out
        notices = run(engine, steps)
        verdicts = redactions.verdicts(engine)
        images = _draw(engine, regions, drawn_pages=drawn_pages)

    redaction_per_span = {
        span_id: Redaction(gone, hidden[span_id]) for span_id, gone in verdicts.items()
    }
    return Rendered(
        images,
        fits={span_id: fitted.report for span_id, fitted in fits.replaces.items()},
        insert_fits={position: fitted.report for position, fitted in fits.inserts.items()},
        skipped=resolved.skipped,
        notices=notices + _said_unredacted(verdicts),
        redactions=redaction_per_span,
    )


def _redacted_by(steps: list[Step]) -> list[Span]:
    """The spans the steps redact, in order."""
    return [step.span for step in steps if isinstance(step, Erase)]


def _said_unredacted(verdicts: dict[str, bool]) -> list[Notice]:
    """A notice for each redaction drawn whose words are still in the file, on the page or not.

    Said now, while the user can still undo it: export refuses the file.
    """
    return [
        SpanNotice(span_id, Message("not_redacted"))
        for span_id, gone in verdicts.items()
        if not gone
    ]


def _check_region_count(regions: list[Region]) -> None:
    """Refuse more regions than a render draws, before anything is read."""
    # Read as a module attribute, so a test can lower the limit.
    if len(regions) > constants.MAX_REGIONS:
        raise InvalidRequest(debug=f"regions: {len(regions)}, at most {constants.MAX_REGIONS}")


def _check_regions(regions: list[Region], pages: list[Page]) -> None:
    """Refuse a region the document can't give, or rows of a page asked for twice."""
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
    for page_number, page_strips in strips.items():
        _check_apart(page_number, page_strips)


def _check_apart(page_number: int, strips: list[Rect]) -> None:
    """Refuse two strips of a page that share rows: the browser joins those into one."""
    ordered = sorted(strips, key=lambda strip: strip.y0)
    for above, below in pairwise(ordered):
        if below.y0 < above.y1:
            debug = f"regions: two on page {page_number} share the rows from {below.y0:g}"
            raise InvalidRequest(debug=debug)


def _check_rows(regions: list[Region], drawn_pages: dict[int, DrawnPage]) -> None:
    """Refuse a page's strips whose whole pixel rows add up past their limit.

    Each is drawn out to whole rows, so thin slivers would add up to many times the page.
    """
    asked: Counter[int] = Counter()
    for region in regions:
        drawn_page = drawn_pages[region.page]
        strip = _strip_of(region, drawn_page.page)
        asked[region.page] += len(_pixel_rows(strip, drawn_page.scale))
    for number, rows in asked.items():
        page, scale = drawn_pages[number].page, drawn_pages[number].scale
        page_rows = len(_pixel_rows(Rect(0.0, 0.0, page.width, page.height), scale))
        most = constants.MAX_STRIP_ROWS_PER_PAGE_ROW * page_rows
        if rows > most:
            debug = f"regions: {rows} pixel rows on page {number}, at most {most}"
            raise InvalidRequest(debug=debug)


def _strip_of(region: Region, page: Page) -> Rect:
    """The rows a region asks for, full width, cut to the page's own."""
    top = 0.0 if region.y0 is None else max(region.y0, 0.0)
    bottom = page.height if region.y1 is None else min(region.y1, page.height)
    return Rect(0.0, top, page.width, bottom)


def _reply_body(rendered: Rendered, expires_at: float, said_in: str) -> Render:
    """What render worked out, as the browser gets it, in the reader's words."""
    return {
        "images": rendered.images,
        "fits": {span_id: fit_info(fit, said_in) for span_id, fit in rendered.fits.items()},
        "insert_fits": [
            {**fit_info(fit, said_in), "edit": position}
            for position, fit in rendered.insert_fits.items()
        ],
        "redactions": {
            span_id: redaction_info(redaction, said_in)
            for span_id, redaction in rendered.redactions.items()
        },
        "skipped": [skipped_info(skipped, said_in) for skipped in rendered.skipped],
        "notices": [notice_info(notice, said_in) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": time_of(expires_at),
    }


def _draw(
    engine: Engine, regions: list[Region], *, drawn_pages: dict[int, DrawnPage]
) -> list[ImageInfo]:
    """Each region as a base64 PNG, in the order asked, drawing each page once."""
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
    """One page's regions as base64 PNGs: the whole page, or full-width strips of it."""
    page, scale = drawn_page.page, drawn_page.scale
    number = regions[0].page
    # No two regions of a page share rows (_check_apart), so a whole page is its only one.
    whole_page = regions[0].y0 is None and regions[0].y1 is None
    if whole_page:
        return [_image_of(number, 0.0, engine.page_image(number, scale))]
    # Strips, out to whole pixels so their rows are the page image's rows.
    boxes = [_whole_rows(_strip_of(region, page), scale) for region in regions]
    pngs = engine.box_images(number, scale, boxes)
    return [_image_of(number, box.y0, png) for box, png in zip(boxes, pngs, strict=True)]


def _whole_rows(strip: Rect, scale: float) -> Rect:
    """The strip grown out to whole pixel rows at `scale`."""
    rows = _pixel_rows(strip, scale)
    return Rect(strip.x0, rows.start / scale, strip.x1, rows.stop / scale)


def _pixel_rows(strip: Rect, scale: float) -> range:
    """The page image's pixel rows the strip touches at `scale`."""
    return range(math.floor(strip.y0 * scale), math.ceil(strip.y1 * scale))


def _image_of(page: int, y: float, png: bytes) -> ImageInfo:
    """One image as the browser gets it: its page, its top, and the PNG in base64."""
    return {"page": page, "y": y, "image": base64.b64encode(png).decode()}
