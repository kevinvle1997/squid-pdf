"""Page sizes and images: what the browser lays out and shows."""

from __future__ import annotations

import pymupdf

from squidpdf.core import Rect
from tests.helpers import assert_equal, assert_true

_A4 = (595.0, 842.0)  # points; pymupdf's default new_page()
_SCALE = 2
_STRIP = Rect(0, 80, _A4[0], 100)  # one full-width row, as render will ask for


def test_pages_are_sized_in_points(engine):
    """The browser lays pages out from these before any image arrives."""
    assert_equal(engine.pages(), [_A4, _A4], "page sizes of the fixture")


def test_a_page_image_is_a_white_png_at_the_asked_scale(engine):
    """No alpha: the page is white in both themes (Rule 2)."""
    png = engine.page_image(0, _SCALE)
    assert_true(png.startswith(b"\x89PNG"), "page image is a PNG")
    pix = pymupdf.Pixmap(png)
    expected = (_A4[0] * _SCALE, _A4[1] * _SCALE)
    assert_equal((pix.width, pix.height), expected, f"pixels at scale {_SCALE}")
    assert_equal(pix.alpha, 0, "alpha channel")


def test_a_clip_gives_only_that_strip(engine):
    """Render sends strips of the changed rows, not whole pages."""
    pix = pymupdf.Pixmap(engine.page_image(0, _SCALE, _STRIP))
    expected = (_STRIP.width * _SCALE, _STRIP.height * _SCALE)
    assert_equal((pix.width, pix.height), expected, f"pixels in a strip at scale {_SCALE}")
