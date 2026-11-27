from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from squidpdf.core import BUILD, Page, Reply, Workers
from squidpdf.documents import constants, store
from squidpdf.documents.errors import NoSuchPage, is_page
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import Loaded


@dataclass(frozen=True, slots=True, eq=False)
class PageController:
    workers: Workers
    documents: DocumentStore

    async def page(self, doc: Loaded, *, page: int, scale: int, build: str) -> Reply[bytes]:
        pages = await asyncio.to_thread(store.load_pages, doc.folder)
        if not is_page(page, len(pages)):
            raise NoSuchPage()
        png = await self._enqueue_draw_page(
            doc.folder, page=page, scale=page_scale(pages[page], scale)
        )
        # An old `build` still gets the image, but not to keep: its bytes may change.
        cache = constants.PAGE_CACHE if build == BUILD else "no-store"
        return Reply(png, {"Cache-Control": cache})

    async def _enqueue_draw_page(self, folder: Path, *, page: int, scale: float) -> bytes:
        task = partial(_draw_page, self.documents, folder, page=page, scale=scale)
        return await self.workers.run(constants.PAGE_IMAGE_TIMEOUT_S, task)


def _draw_page(documents: DocumentStore, folder: Path, *, page: int, scale: float) -> bytes:
    with documents.open_original(folder) as engine:
        return engine.page_image(page, scale)


def page_scale(page: Page, scale: int) -> float:
    """MuPDF rounds each side up a pixel: solves (width s + 1)(height s + 1) = limit for s."""
    width, height = page.width, page.height
    area, rim = width * height, width + height
    most_pixels = constants.MAX_IMAGE_PIXELS - 1
    largest = (math.sqrt(rim**2 + 4 * area * most_pixels) - rim) / (2 * area)
    return min(scale, largest)
