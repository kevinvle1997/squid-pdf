"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import base64
import math
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from squidpdf.core import BUILD, Engine, InvalidRequest, Page, Rect, Workers, open_pdf, words
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.pages import page_scale
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply, log_fits
from squidpdf.editing.constants import RENDER_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.info import fit_info, notice_info, skipped_info
from squidpdf.editing.types import ImageInfo, Region, Render, Rendered, RenderReply

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
    ) -> RenderReply:
        """Each region drawn with its page's edits, a fit per edit, and what was skipped.

        Refuses edits over the limits and regions the document lacks. A
        redaction pointing at nothing fails the whole request.
        """
        check_edits(edits)
        pages = store.load_pages(doc.folder)
        check_regions(regions, pages)
        # Each strip at its page image's scale, so the two line up.
        scales = {region.page: page_scale(pages[region.page], scale) for region in regions}
        rendered = await self._enqueue_draw_regions(
            doc.folder, edits=edits, regions=regions, scales=scales
        )
        body = reply_body(rendered, doc.expires_at, said_in)
        return RenderReply(body, words.language_headers(said_in))

    async def _enqueue_draw_regions(
        self,
        folder: Path,
        *,
        edits: list[Edit],
        regions: list[Region],
        scales: dict[int, float],
    ) -> Rendered:
        """Draw the regions on a worker."""
        task = partial(
            draw_regions,
            str(folder),
            edits=edits,
            regions=regions,
            scales=scales,
        )
        return await self._workers.run(RENDER_TIMEOUT_S, task)


def draw_regions(
    folder: str, *, edits: list[Edit], regions: list[Region], scales: dict[int, float]
) -> Rendered:
    """Apply the edits on the drawn pages, then draw each region. Runs in a worker.

    `scales` is each page's pixels per point, the same as its page image.
    """
    path = Path(folder)
    index = store.load_index(path)
    if index is None:  # only a sweep removes it
        raise Gone
    pages = store.load_pages(path)

    with open_pdf(str(path / store.ORIGINAL)) as engine:
        fits = log_fits(engine, edits, index)  # before apply: remove() can drop the fonts
        applied = apply(engine, edits, index, pages={region.page for region in regions})
        images = [
            draw(engine, region, page=pages[region.page], scale=scales[region.page])
            for region in regions
        ]

    return Rendered(images, fits, applied.skipped, applied.notices)


def check_regions(regions: list[Region], pages: list[Page]) -> None:
    """Refuse a region the document can't give: a page it lacks, or a top below its bottom."""
    for region in regions:
        if not 0 <= region.page < len(pages):
            raise NoSuchPage(debug=f"regions: no page {region.page}")
        top = 0.0 if region.y0 is None else region.y0
        bottom = pages[region.page].height if region.y1 is None else region.y1
        # `not <` rather than `>=`: every comparison with NaN is false, so NaN fails too.
        if not top < bottom:
            reason = f"regions: y0 must be a number above y1 on page {region.page}"
            raise InvalidRequest(debug=reason)


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
        "redactions": [],  # verdicts come with export
        "skipped": [skipped_info(skipped, said_in) for skipped in rendered.skipped],
        "notices": [notice_info(notice, said_in) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
    }


def draw(engine: Engine, region: Region, *, page: Page, scale: float) -> ImageInfo:
    """One region as a base64 PNG: the whole page, or a full-width strip of it."""
    whole_page = region.y0 is None and region.y1 is None
    if whole_page:
        png = engine.page_image(region.page, scale)
        return {"page": region.page, "y": 0.0, "image": base64.b64encode(png).decode()}
    # A strip, out to whole pixels so its rows are the page image's rows.
    y0 = 0.0 if region.y0 is None else max(region.y0, 0.0)
    top = math.floor(y0 * scale) / scale
    y1 = page.height if region.y1 is None else min(region.y1, page.height)
    bottom = math.ceil(y1 * scale) / scale
    png = engine.page_image(region.page, scale, Rect(0, top, page.width, bottom))
    return {"page": region.page, "y": top, "image": base64.b64encode(png).decode()}
