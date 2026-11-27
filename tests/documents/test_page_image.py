from __future__ import annotations

import math

from squidpdf.api.constants import PAGE_SCALES
from squidpdf.core import Page
from squidpdf.documents.constants import MAX_IMAGE_PIXELS
from squidpdf.documents.page_image import page_scale
from tests.helpers import assert_at_least, assert_at_most

# Not through the API: it can outlast the page image's timeout on a busy machine.
_HAIRLINE = Page(1, 30_000_000, 0)


def test_a_hairline_a_mile_long_is_drawn_a_pixel_across_within_the_pixel_limit():
    scale = page_scale(_HAIRLINE, max(PAGE_SCALES))
    across = math.ceil(_HAIRLINE.width * scale)
    down = math.ceil(_HAIRLINE.height * scale)
    assert_at_least(across, 1, f"pixels across, at {scale} per point")
    assert_at_most(across * down, MAX_IMAGE_PIXELS, "pixels in the image")
