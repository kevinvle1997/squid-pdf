"""A page of the original as an image, and how big it's drawn; render's strips match it."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from squidpdf.core import BUILD, Page, Reply, Workers
from squidpdf.documents import store
from squidpdf.documents.constants import MAX_IMAGE_PIXELS, PAGE_CACHE, PAGE_IMAGE_TIMEOUT_S
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.types import Loaded


@dataclass(frozen=True, slots=True, eq=False)
class PageController:
    """A page image, from request to reply."""

    workers: Workers  # where it draws, off the server's own thread

    async def page(self, doc: Loaded, *, page: int, scale: int, build: str) -> Reply[bytes]:
        """Page `page` as a PNG, at `scale` or less if the page is very large."""
        # Read and parsed from disk: off the server's thread.
        pages = await asyncio.to_thread(store.load_pages, doc.folder)
        if not 0 <= page < len(pages):
            raise NoSuchPage()
        png = await self._enqueue_draw_page(
            doc.folder, page=page, scale=page_scale(pages[page], scale)
        )
        # An old `build` still gets the image, but not to keep: its bytes may change.
        cache = PAGE_CACHE if build == BUILD else "no-store"
        return Reply(png, {"Cache-Control": cache})

    async def _enqueue_draw_page(self, folder: Path, *, page: int, scale: float) -> bytes:
        """Draw the page on a worker."""
        task = partial(_draw_page, str(folder), page=page, scale=scale)
        return await self.workers.run(PAGE_IMAGE_TIMEOUT_S, task)


def _draw_page(folder: str, page: int, scale: float) -> bytes:
    """Draw one page of the original, unrotated. Runs in a worker."""
    with store.open_original(Path(folder)) as engine:
        return engine.page_image(page, scale)


def page_scale(page: Page, scale: int) -> float:
    """`scale`, or less if the page would go over the pixel limit.

    MuPDF rounds each side up to a whole pixel, so the image can be a pixel
    more a side: the largest scale is where (width s + 1)(height s + 1) meets
    the limit, solved for s. Always above 0, however thin the page.
    """
    width, height = page.width, page.height
    area, rim = width * height, width + height
    largest = (math.sqrt(rim**2 + 4 * area * (MAX_IMAGE_PIXELS - 1)) - rim) / (2 * area)
    return min(scale, largest)
