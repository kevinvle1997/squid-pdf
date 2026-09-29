"""How big a page's image is drawn. Render's strips use it too, so they line up."""

from __future__ import annotations

import math

from squidpdf.core import Page
from squidpdf.documents.constants import MAX_IMAGE_PIXELS


def page_scale(page: Page, scale: int) -> float:
    """`scale`, or less if the page would go over the pixel limit."""
    width, height = page.width, page.height
    # Less a pixel a side: MuPDF rounds each side up, which could tip it over.
    largest = math.sqrt(MAX_IMAGE_PIXELS / (width * height)) - 1 / min(width, height)
    return min(scale, largest)
