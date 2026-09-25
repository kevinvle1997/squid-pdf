"""Shared fixtures for every test under tests/, however deep."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import MuPDFEngine


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """Page 1 references its fonts; page 2 embeds one, subsets it, and uses it twice."""
    path = tmp_path_factory.mktemp("fx") / "sample.pdf"
    doc = pymupdf.open()

    p1 = doc.new_page()
    p1.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    p1.insert_text(
        (72, 128),
        "Made on 14 March 2026 between Wescott and Rowe.",
        fontname="tiro",
        fontsize=11,
    )

    p2 = doc.new_page()
    p2.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    p2.insert_text(
        (72, 96),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    p2.insert_text(
        (72, 124), "Invoices are due within thirty days.", fontname="emb", fontsize=11
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def engine(pdf):
    with MuPDFEngine(pdf) as eng:
        yield eng
