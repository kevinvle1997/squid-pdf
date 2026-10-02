"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
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
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.page_image import page_scale
from squidpdf.documents.replies import time_of
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import Erase, Step, is_page, log_fits, plan, resolve, run
from squidpdf.editing.constants import RENDER_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.replies import fit_info, notice_info, skipped_info
from squidpdf.editing.types import (
    DrawnPage,
    ImageInfo,
    Notice,
    Region,
    Render,
    Rendered,
    SpanNotice,
)

__all__ = [
    "RenderController",
]


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

        Refuses edits over the limits and regions the document lacks. A
        redaction pointing at nothing fails the whole request.
        """
        check_edits(edits)
        pages = store.load_pages(doc.folder)
        check_regions(regions, pages)
        # Only the pages drawn go to the worker, so it needn't read the page list again,
        # each at its page image's scale, so a strip and the image line up.
        drawn_pages = {
            region.page: DrawnPage(pages[region.page], page_scale(pages[region.page], scale))
            for region in regions
        }
        rendered = await self._enqueue_draw_regions(
            doc.folder, edits=edits, regions=regions, drawn_pages=drawn_pages
        )
        body = reply_body(rendered, doc.expires_at, said_in)
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
            draw_regions, str(folder), edits=edits, regions=regions, drawn_pages=drawn_pages
        )
        return await self.workers.run(RENDER_TIMEOUT_S, task)


def draw_regions(
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
            strip_of(region, drawn_pages[region.page].page)
        )

    with store.open_original(path) as engine:
        resolved = resolve(engine, edits, index)
        fits = log_fits(engine, resolved)  # before run: erasing can drop the fonts it measures
        steps = plan(engine, resolved, strips=strips)
        notices = run(engine, steps) + said_unredacted(engine, steps)
        images = [
            draw(engine, region, drawn_page=drawn_pages[region.page]) for region in regions
        ]

    return Rendered(images, fits, resolved.skipped, notices)


def said_unredacted(engine: Engine, steps: list[Step]) -> list[Notice]:
    """A notice for each redaction drawn whose text is still there, as a form field's is.

    Said now, while the user can still undo it: export refuses the file.
    """
    redacted = tuple(step.span for step in steps if isinstance(step, Erase))
    verdicts = RedactionController(redacted).verdicts(engine)
    return [
        SpanNotice(span_id, Message("form_field_not_redacted"))
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
            debug = f"regions: y0 above y1, and on page {region.page}'s 0 to {page.height:g}"
            raise InvalidRequest(debug=debug)


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


def draw(engine: Engine, region: Region, *, drawn_page: DrawnPage) -> ImageInfo:
    """One region as a base64 PNG: the whole page, or a full-width strip of it."""
    page, scale = drawn_page.page, drawn_page.scale
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
