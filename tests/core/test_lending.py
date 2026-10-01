"""Other copies of a font lend letters only when a line needs them."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import open_pdf
from squidpdf.core.fonts import FACES, face_bytes
from squidpdf.core.google import GoogleFile
from squidpdf.core.mupdf import MuPDFDriver
from squidpdf.core.types import PageFont
from tests.core.conftest import POPPINS
from tests.helpers import assert_equal, assert_in

_SIZE = 14.0

# One trimmed copy of Times per page. Page 0's lacks Y, a and y; page 1's has all
# three; page 2's alone has J. Each shares enough letters with page 0's to be vouched for.
_TEXTS = ("Hello there", "Yearly quiz", "Hello Jim, three")


def _copy_per_page(path: str) -> str:
    """Three one-page PDFs, each with its own trimmed copy of Times, joined into one.

    As merging documents leaves them: each page keeps the copy it came with,
    trimmed (subset) to its own letters, under one name with its own prefix.
    """
    joined = pymupdf.open()
    for text in _TEXTS:
        single = pymupdf.open()
        page = single.new_page()
        # "own" is only the name the page files the font under.
        page.insert_font(fontname="own", fontbuffer=pymupdf.Font("tiro").buffer)
        page.insert_text((72, 96), text, fontname="own", fontsize=_SIZE)
        single.subset_fonts(verbose=False)
        # Saved and reopened, as separate files would be, before joining.
        joined.insert_pdf(pymupdf.open("pdf", single.tobytes()))
    joined.save(path)
    return path


@pytest.fixture
def noted(monkeypatch) -> tuple[list[int], list[int]]:
    """Each font object read out of the file, and each page whose fonts are listed, in order."""
    read_out: list[int] = []
    listed: list[int] = []
    font_bytes, fonts = MuPDFDriver.font_bytes, MuPDFDriver.fonts

    def noted_font_bytes(self: MuPDFDriver, xref: int) -> bytes | None:
        read_out.append(xref)
        return font_bytes(self, xref)

    def noted_fonts(self: MuPDFDriver, page: int) -> list[PageFont]:
        listed.append(page)
        return fonts(self, page)

    monkeypatch.setattr(MuPDFDriver, "font_bytes", noted_font_bytes)
    monkeypatch.setattr(MuPDFDriver, "fonts", noted_fonts)
    return read_out, listed


def _copies(path: str) -> list[int]:
    """Each page's copy of the font, by its object number, page by page."""
    doc = pymupdf.open(path)
    return [xref for number in range(doc.page_count) for xref, *_ in doc[number].get_fonts()]


def test_a_copy_a_line_doesnt_need_is_never_opened(tmp_path, noted):
    path = _copy_per_page(str(tmp_path / "copies.pdf"))
    first, second, third = _copies(path)
    read_out, listed = noted

    with open_pdf(path) as engine:
        span = next(span for span in engine.index() if span.page == 0)
        read_out.clear()
        listed.clear()
        # Page 0's own copy draws every letter: no other page or copy is looked at.
        own_only = engine.missing(span, "Hello other")
        after_own = (list(read_out), list(listed))
        # Y, a and y come from page 1's copy, the nearest that has them: page 2's stays shut.
        borrowed = engine.missing(span, "Yearly Hello")
        after_borrowing = (list(read_out), list(listed))
        # The browser's list of letters is the whole pool, page 2's J with it.
        widths = engine.widths(span)

    assert_equal(own_only, [], "letters page 0's copy lacks for its own words")
    looked_at = "copies read out, and pages listed"
    assert_equal(after_own, ([first], [0]), looked_at)
    assert_equal(borrowed, [], "letters no copy draws")
    assert_equal(after_borrowing, ([first, second], [0, 1]), looked_at)
    assert_in("J", widths, "letters the browser is told the pool draws")
    assert_in(third, read_out, "copies read out once the whole pool is asked for")


def _missing_with_google(path: str, font_file: bytes) -> list[str]:
    """What the first span can't draw of "Yearly Hello", with `font_file` as Google's copy."""

    def fetch(_file: GoogleFile) -> bytes:
        return font_file

    with open_pdf(path, fetch=fetch) as engine:
        span = next(iter(engine.index()))
        return engine.missing(span, "Yearly Hello")


def test_googles_copy_is_measured_by_its_bytes_not_its_name(poppins_subset):
    """A process keeps what Google's copies draw, and must not answer for other bytes."""
    missing_with_google = _missing_with_google(poppins_subset, POPPINS.read_bytes())
    # Liberation Sans handed back under Poppins' name: other widths, so it lends nothing.
    missing_with_impostor = _missing_with_google(
        poppins_subset, face_bytes(FACES["Liberation Sans Regular"])
    )

    assert_equal(missing_with_google, [], "letters Google's Poppins leaves missing")
    assert_equal(
        missing_with_impostor, ["Y", "a", "y"], "letters another font by its name leaves"
    )
