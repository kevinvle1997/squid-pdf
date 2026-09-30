"""A page image as large as asked, or as large as the pixel limit allows."""

from __future__ import annotations

import math

import pytest

from squidpdf.core import Page
from squidpdf.documents.constants import MAX_IMAGE_PIXELS
from squidpdf.documents.pages import page_scale
from tests.helpers import assert_at_most, assert_equal, assert_true

_ASKED = 4  # pixels per point, the most a route accepts


@pytest.mark.parametrize(
    ("width", "height"),
    [(612, 792), (14_400, 14_400), (1, 30_000_000)],
    ids=["a letter page, well under", "a poster, over", "a hairline a mile long, far over"],
)
def test_a_page_is_drawn_as_large_as_the_pixel_limit_allows(width, height):
    scale = page_scale(Page(width, height, 0), _ASKED)
    pixels = math.ceil(width * scale) * math.ceil(height * scale)
    assert_true(0 < scale <= _ASKED, f"{scale} pixels per point")
    assert_at_most(pixels, MAX_IMAGE_PIXELS, "pixels in the image")
    if width * height * _ASKED**2 < MAX_IMAGE_PIXELS / 2:
        assert_equal(scale, _ASKED, "the scale of a page well under the limit")
