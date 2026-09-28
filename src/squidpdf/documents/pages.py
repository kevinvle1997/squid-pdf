"""How big a page's image is drawn: the scale asked for, or less for a very large page.

Framework-free, so render's strips can be drawn at the same scale as the page
image they're laid over, and line up with it.
"""

from __future__ import annotations

import math

from squidpdf.core import Page
from squidpdf.documents.constants import MAX_IMAGE_PIXELS


def page_scale(page: Page, scale: int) -> float:
    """`scale`, or the largest that keeps this page under the pixel limit.

    Render's strips use it too, so they line up with the page image.
    """
    width, height = page.width, page.height
    # Less a pixel a side: MuPDF rounds each side up, which could tip it over.
    largest = math.sqrt(MAX_IMAGE_PIXELS / (width * height)) - 1 / min(width, height)
    return min(scale, largest)
