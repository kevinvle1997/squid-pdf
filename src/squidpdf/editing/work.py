"""Pool work for render: the edits on the pages being drawn, applied, then drawn.

Framework-free and handed only a path and dataclasses: an open document doesn't
pickle, and a test can call it directly.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

from squidpdf.core import Engine, MuPDFEngine, Page, Rect
from squidpdf.documents import store
from squidpdf.editing.apply import apply, fits
from squidpdf.editing.edits import Edit
from squidpdf.editing.fit import FitCheck
from squidpdf.editing.types import FitInfo, ImageInfo, Region, Rendered


def render(
    folder: str, edits: list[Edit], regions: list[Region], scales: dict[int, float]
) -> Rendered:
    """Each region drawn with the edits on its page, and a fit for every replaced span.

    `scales` is each drawn page's pixels per point, clamped as its page image is,
    so a strip lines up with it. The index is the saved one: rebuilt, every id
    would change.
    """
    path = Path(folder)
    index = store.load_index(path)
    if index is None:  # upload saves it before it answers
        raise LookupError(f"no index in {folder}")
    pages = store.load_pages(path)

    with MuPDFEngine(str(path / store.ORIGINAL)) as engine:
        checked = fits(engine, edits, index)  # before apply: remove() can drop the fonts
        applied = apply(engine, edits, index, pages={region.page for region in regions})
        images = [
            _draw(engine, region, pages[region.page], scales[region.page]) for region in regions
        ]

    return {
        "images": images,
        "fits": {span_id: _fit(fit) for span_id, fit in checked.items()},
        "redactions": [],  # verdicts come with export and the redaction check
        "skipped": applied.skipped,
        "notices": applied.notices,
    }


def _draw(engine: Engine, region: Region, page: Page, scale: float) -> ImageInfo:
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


def _fit(fit: FitCheck) -> FitInfo:
    """A fit as the browser gets it: option names, since it has their sentences."""
    return {
        "delta_pt": fit.delta_pt,
        "missing": fit.missing,
        "options": [o.name for o in fit.options],
        "strategy": fit.strategy,
        "message": fit.describe(),
    }
