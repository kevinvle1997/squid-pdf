"""Shared fixtures for every test under tests/, however deep."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import MuPDFEngine

# The sample's two pages, counted from 0 as spans count them.
REFERENCED_PAGE = 0  # fonts named but not in the file: edits use a stand-in
EMBEDDED_PAGE = 1  # one font in the file, trimmed to the letters the page uses


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """A two-page contract with one kind of font on each page.

    Page 1 only names its fonts: "tibo" and "tiro" are MuPDF's short names for
    Times Bold and Times Roman, which it never puts in the file. Page 2 puts
    Times Roman in the file, uses it for two lines, then trims it to the
    letters those lines use, so a letter like é is left with no shape.
    """
    path = tmp_path_factory.mktemp("fx") / "sample.pdf"
    doc = pymupdf.open()

    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        "Made on 14 March 2026 between Wescott and Rowe.",
        fontname="tiro",
        fontsize=11,
    )

    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text(
        (72, 96),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    embedded.insert_text(
        (72, 124), "Invoices are due within thirty days.", fontname="emb", fontsize=11
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def engine(pdf):
    """The sample, open in the engine for one test."""
    with MuPDFEngine(pdf) as eng:
        yield eng
