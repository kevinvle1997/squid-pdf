"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import base64
import math
from functools import partial
from pathlib import Path

from squidpdf.core import (
    BUILD,
    Engine,
    InvalidRequest,
    Message,
    Page,
    Rect,
    Reply,
    Workers,
    words,
)
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.info import time_of
from squidpdf.documents.pages import page_scale
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import Erase, Step, is_page, log_fits, plan, resolve, run
from squidpdf.editing.constants import RENDER_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.info import fit_info, notice_info, skipped_info
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import ImageInfo, Notice, Region, Render, Rendered

__all__ = [
    "RenderController",
]


class RenderController:
    """Render, from request to reply."""

    def __init__(self, workers: Workers) -> None:
        """Draw on `workers`, off the server's own thread."""
        self._workers = workers

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

        Refuses edits over the limits and regions the document lacks. A
        redaction pointing at nothing fails the whole request.
        """
        check_edits(edits)
        pages = store.load_pages(doc.folder)
        check_regions(regions, pages)
        # Only the pages drawn go to the worker, so it needn't read the page list again.
        drawn = {region.page: pages[region.page] for region in regions}
        # Each strip at its page image's scale, so the two line up.
        scales = {page: page_scale(size, scale) for page, size in drawn.items()}
        rendered = await self._enqueue_draw_regions(
            doc.folder, edits=edits, regions=regions, pages=drawn, scales=scales
        )
        body = reply_body(rendered, doc.expires_at, said_in)
        return Reply(body, words.language_headers(said_in))

    async def _enqueue_draw_regions(
        self,
        folder: Path,
        *,
        edits: list[Edit],
        regions: list[Region],
        pages: dict[int, Page],
        scales: dict[int, float],
    ) -> Rendered:
        """Draw the regions on a worker."""
        task = partial(
            draw_regions,
            str(folder),
            edits=edits,
            regions=regions,
            pages=pages,
            scales=scales,
        )
        return await self._workers.run(RENDER_TIMEOUT_S, task)


def draw_regions(
    folder: str,
    *,
    edits: list[Edit],
    regions: list[Region],
    pages: dict[int, Page],
    scales: dict[int, float],
) -> Rendered:
    """Apply the edits the regions show, then draw each region. Runs in a worker.

    `pages` are the drawn pages' sizes, and `scales` each one's pixels per
    point, the same as its page image.
    """
    path = Path(folder)
    index = store.load_index(path)
    if index is None:  # analysed at upload, so a sweep or a delete removed it
        raise Gone
    strips: dict[int, list[Rect]] = {}
    for region in regions:
        strips.setdefault(region.page, []).append(strip_of(region, pages[region.page]))

    with store.open_original(path) as engine:
        resolved = resolve(engine, edits, index)
        fits = log_fits(engine, resolved)  # before run: erasing can drop the fonts it measures
        steps = plan(engine, resolved, strips=strips)
        notices = run(engine, steps) + said_unredacted(engine, steps)
        images = [
            draw(engine, region, page=pages[region.page], scale=scales[region.page])
            for region in regions
        ]

    return Rendered(images, fits, resolved.skipped, notices)


def said_unredacted(engine: Engine, steps: list[Step]) -> list[Notice]:
    """A notice for each redaction drawn whose text is still there, as a form field's is.

    Said now, while the user can still undo it: export refuses the file.
    """
    redacted = [step.span for step in steps if isinstance(step, Erase)]
    verdicts = RedactionController(redacted).verdicts(engine)
    return [
        Notice(span_id, Message("form_field_not_redacted"))
        for span_id, gone in verdicts.items()
        if not gone
    ]


def check_regions(regions: list[Region], pages: list[Page]) -> None:
    """Refuse a region the document can't give: a page it lacks, or no rows of the page."""
    for region in regions:
        if not is_page(region.page, len(pages)):
            raise NoSuchPage(debug=f"regions: no page {region.page}")
        page = pages[region.page]
        strip = strip_of(region, page)
        # `not <` rather than `>=`: every comparison with NaN is false, so NaN fails too.
        if not strip.y0 < strip.y1:
            reason = f"regions: y0 above y1, and on page {region.page}'s 0 to {page.height:g}"
            raise InvalidRequest(debug=reason)


def strip_of(region: Region, page: Page) -> Rect:
    """The rows a region asks for, full width, cut to the page's own."""
    top = 0.0 if region.y0 is None else max(region.y0, 0.0)
    bottom = page.height if region.y1 is None else min(region.y1, page.height)
    return Rect(0.0, top, page.width, bottom)


def reply_body(rendered: Rendered, expires_at: float, said_in: str) -> Render:
    """What render worked out, as the browser gets it, in the reader's words."""
    fits = rendered.fits
    return {
        "images": rendered.images,
        "fits": {span_id: fit_info(fit, said_in) for span_id, fit in fits.replaces.items()},
        "insert_fits": [
            {**fit_info(fit, said_in), "edit": position}
            for position, fit in fits.inserts.items()
        ],
        # Its shape comes with partial redaction; a redaction left in place is a notice now.
        "redactions": [],
        "skipped": [skipped_info(skipped, said_in) for skipped in rendered.skipped],
        "notices": [notice_info(notice, said_in) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": time_of(expires_at),
    }


def draw(engine: Engine, region: Region, *, page: Page, scale: float) -> ImageInfo:
    """One region as a base64 PNG: the whole page, or a full-width strip of it."""
    whole_page = region.y0 is None and region.y1 is None
    if whole_page:
        png = engine.page_image(region.page, scale)
        return {"page": region.page, "y": 0.0, "image": base64.b64encode(png).decode()}
    # A strip, out to whole pixels so its rows are the page image's rows.
    strip = strip_of(region, page)
    top = math.floor(strip.y0 * scale) / scale
    bottom = math.ceil(strip.y1 * scale) / scale
    png = engine.page_image(region.page, scale, Rect(0, top, page.width, bottom))
    return {"page": region.page, "y": top, "image": base64.b64encode(png).decode()}
