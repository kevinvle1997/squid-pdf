"""Extraction: finding spans, and keeping their ids stable."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from squidpdf.core import MuPDFEngine
from tests.helpers import assert_any, assert_equal, assert_true

_GREY = 128  # any picture will do; this one is a plain grey square


def _one_line_page(path: Path, with_image: bool) -> str:
    """A page with one line of text, and a picture beside it if asked."""
    doc = pymupdf.open()
    page = doc.new_page()
    if with_image:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(_GREY)
        page.insert_image(pymupdf.Rect(72, 150, 272, 350), pixmap=pix)
    page.insert_text((72, 96), "Figure 1 shows the site plan.", fontname="tiro", fontsize=11)
    doc.save(path)
    return str(path)


def test_index_finds_text(engine):
    index = engine.index()
    assert_true(len(index) >= 3, f"expected at least 3 spans, found {len(index)}")
    assert_any(index, lambda s: "14 March 2026" in s.text, describe=lambda s: repr(s.text))


def test_span_ids_are_stable_across_reindexing(pdf):
    """The client holds these. They must not move."""
    with MuPDFEngine(pdf) as a, MuPDFEngine(pdf) as b:
        assert_equal(
            [s.id for s in a.index()],
            [s.id for s in b.index()],
            "span ids across two indexes of the same pristine document",
        )


def test_an_image_on_the_page_changes_no_span(tmp_path):
    """The index skips images rather than decoding them. The spans must not notice."""
    plain_path = _one_line_page(tmp_path / "plain.pdf", with_image=False)
    pictured_path = _one_line_page(tmp_path / "pictured.pdf", with_image=True)

    with MuPDFEngine(plain_path) as plain, MuPDFEngine(pictured_path) as pictured:
        assert_equal(
            list(pictured.index()),
            list(plain.index()),
            "spans on a page with an image, against the same page without",
        )
