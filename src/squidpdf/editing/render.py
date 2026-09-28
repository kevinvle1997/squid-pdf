"""Render, end to end: the browser's edits drawn on the rows it's showing, with a fit for each.

RenderController checks the request, sends the drawing to the workers and hands
back what it worked out, in no one's words yet: `editing/api.py` puts that into
the reader's. Nothing here imports the web framework, so a worker can import
it to run the drawing.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

from squidpdf.core import Engine, InvalidRequest, Page, Rect, Workers, open_pdf
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.pages import page_scale
from squidpdf.editing.apply import apply, log_fits
from squidpdf.editing.constants import MAX_EDITS, MAX_TEXT_CHARS, RENDER_TIMEOUT_S
from squidpdf.editing.edits import Edit, Insert, Replace
from squidpdf.editing.errors import TextTooLong, TooManyEdits
from squidpdf.editing.types import ImageInfo, Region, Rendered

__all__ = [
    "RenderController",
]


class RenderController:
    """Render, from the request to the drawn rows, over the app's workers."""

    def __init__(self, workers: Workers) -> None:
        """Draw on `workers`, never on the server's own thread."""
        self._workers = workers

    async def render(
        self, folder: Path, edits: list[Edit], regions: list[Region], scale: int
    ) -> Rendered:
        """Each region drawn with the edits on its page, a fit per edit, and the skips.

        Keeps nothing. Refuses an edit list over the limits, and a region the
        document can't give. A redaction pointing at nothing fails the whole
        request: the worker raises BadReference.
        """
        if len(edits) > MAX_EDITS:
            raise TooManyEdits(MAX_EDITS)
        too_long = any(
            isinstance(edit, Replace | Insert) and len(edit.text) > MAX_TEXT_CHARS
            for edit in edits
        )
        if too_long:
            raise TextTooLong(MAX_TEXT_CHARS)
        pages = store.load_pages(folder)
        for region in regions:
            if not 0 <= region.page < len(pages):
                raise NoSuchPage(debug=f"regions: no page {region.page}")
            top = 0.0 if region.y0 is None else region.y0
            bottom = pages[region.page].height if region.y1 is None else region.y1
            # `not <` rather than `>=`: every comparison with NaN is false, so NaN fails too.
            if not top < bottom:
                reason = f"regions: y0 must be a number above y1 on page {region.page}"
                raise InvalidRequest(debug=reason)
        scales = {r.page: page_scale(pages[r.page], scale) for r in regions}
        return await self._workers.run(
            RENDER_TIMEOUT_S, RenderController.work, str(folder), edits, regions, scales
        )

    @staticmethod
    def work(
        folder: str, edits: list[Edit], regions: list[Region], scales: dict[int, float]
    ) -> Rendered:
        """The workers' part: the edits on the drawn pages applied, and each region drawn.

        A staticmethod, so a worker can import it by name. `scales` is each
        drawn page's pixels per point, clamped as its page image is, so a strip
        lines up with it. The index is the saved one: rebuilt, every id would
        change.
        """
        path = Path(folder)
        index = store.load_index(path)
        if index is None:  # upload saves it before it answers, so only a sweep removes it
            raise Gone
        pages = store.load_pages(path)

        with open_pdf(str(path / store.ORIGINAL)) as engine:
            fits = log_fits(engine, edits, index)  # before apply: remove() can drop the fonts
            applied = apply(engine, edits, index, pages={region.page for region in regions})
            images = [
                draw(engine, region, pages[region.page], scales[region.page])
                for region in regions
            ]

        return Rendered(images, fits, applied.skipped, applied.notices)


def draw(engine: Engine, region: Region, page: Page, scale: float) -> ImageInfo:
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
