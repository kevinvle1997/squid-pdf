"""Page sizes and images: what the browser lays out and shows."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import Page, Rect, open_pdf
from tests.helpers import assert_equal, assert_true

_A4_WIDTH, _A4_HEIGHT = 595.0, 842.0  # points; pymupdf's default new_page()
_SCALE = 2
_STRIP = Rect(0, 80, _A4_WIDTH, 100)  # one full-width row, as render will ask for
_INK = 128  # a channel darker than this is text, not paper


@pytest.fixture
def turned(tmp_path) -> str:
    """One A4 page of text whose file asks for a quarter turn."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Turned a quarter", fontname="tiro", fontsize=14)
    page.set_rotation(90)
    path = tmp_path / "turned.pdf"
    doc.save(path)
    return str(path)


def test_pages_are_sized_in_points(engine):
    """The browser lays pages out from these before any image arrives."""
    assert_equal(engine.pages(), [Page(_A4_WIDTH, _A4_HEIGHT, 0)] * 2, "pages of the fixture")


def test_a_page_image_is_a_white_png_at_the_asked_scale(engine):
    """No alpha: the page is white in both themes (Rule 2)."""
    png = engine.page_image(0, _SCALE)
    assert_true(png.startswith(b"\x89PNG"), "page image is a PNG")
    pix = pymupdf.Pixmap(png)
    expected = (_A4_WIDTH * _SCALE, _A4_HEIGHT * _SCALE)
    assert_equal((pix.width, pix.height), expected, f"pixels at scale {_SCALE}")
    assert_equal(pix.alpha, 0, "alpha channel")


def test_a_clip_gives_only_that_strip(engine):
    """Render sends strips of the changed rows, not whole pages."""
    pix = pymupdf.Pixmap(engine.page_image(0, _SCALE, _STRIP))
    expected = (_STRIP.width * _SCALE, _STRIP.height * _SCALE)
    assert_equal((pix.width, pix.height), expected, f"pixels in a strip at scale {_SCALE}")


def test_a_turned_page_stays_unrotated_and_says_its_turn(turned):
    """The browser turns it; everything the server sends stays in one system."""
    with open_pdf(turned) as eng:
        pages = eng.pages()
        pix = pymupdf.Pixmap(eng.page_image(0, _SCALE))
    assert_equal(pages, [Page(_A4_WIDTH, _A4_HEIGHT, 90)], "a quarter-turned page")
    expected = (_A4_WIDTH * _SCALE, _A4_HEIGHT * _SCALE)
    assert_equal((pix.width, pix.height), expected, "pixels of a turned page, unrotated")


def test_on_a_turned_page_the_span_box_covers_its_ink(turned):
    """A box that misses its text would show fidelity on the wrong words."""
    with open_pdf(turned) as eng:
        box = next(iter(eng.index())).bbox
        pix = pymupdf.Pixmap(eng.page_image(0, _SCALE, box))
    darkest = min(pix.samples)
    assert_true(darkest < _INK, f"darkest channel inside the span box is {darkest}")
