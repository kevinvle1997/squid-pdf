"""Shared fixtures for every test under tests/, however deep."""

from __future__ import annotations

import io

import pymupdf
import pytest
from fontTools.ttLib import TTFont

from squidpdf.core import open_pdf
from squidpdf.core.fonts import FACES, face_bytes

_POSTSCRIPT_NAME = 6  # the font's name table entry a PDF names it by

# The sample's two pages, counted from 0 as spans count them.
REFERENCED_PAGE = 0  # fonts named but not in the file: edits use a stand-in
EMBEDDED_PAGE = 1  # one font in the file, trimmed to the letters the page uses


def saved_as(face: str) -> str:
    """The name a saved PDF gives a face we ship, e.g. "Carlito-Bold" for "Carlito Bold".

    Read from the shipped file itself: its PostScript name, which MuPDF writes.
    """
    font = TTFont(io.BytesIO(face_bytes(FACES[face])))
    return str(font["name"].getDebugName(_POSTSCRIPT_NAME))


def stored_file(path: str, page: int, font: str) -> bytes:
    """The font file a saved page stores under the name `font`."""
    doc = pymupdf.open(path)
    [xref] = [xref for xref, _ext, _kind, name, *_ in doc[page].get_fonts() if name == font]
    _name, _ext, _kind, buffer = doc.extract_font(xref)
    return buffer


def named_only(path: str, base_font: str, flags: int | None = None) -> str:
    """One line in a font the file only names, never stores, as Word does with Calibri.

    Written in MuPDF's Helvetica, then renamed: nothing here reads the letters'
    shapes, only the font's name and, when `flags` is given, its description.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Hello there", fontname="helv", fontsize=12)
    [(xref, *_rest)] = page.get_fonts()
    doc.xref_set_key(xref, "BaseFont", f"/{base_font}")
    if flags is not None:
        descriptor = (
            f"<</Type/FontDescriptor/FontName/{base_font}/Flags {flags}/ItalicAngle 0>>"
        )
        doc.xref_set_key(xref, "FontDescriptor", descriptor)
    doc.save(path)
    return path


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


@pytest.fixture(scope="module")
def repeated(tmp_path_factory) -> str:
    """Two pages that open with the same line, as a header on every page does."""
    path = tmp_path_factory.mktemp("repeated") / "repeated.pdf"
    doc = pymupdf.open()
    for _page in range(2):
        doc.new_page().insert_text((72, 72), "CONFIDENTIAL", fontname="helv", fontsize=10)
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def engine(pdf):
    """The sample, open in the engine for one test."""
    with open_pdf(pdf) as eng:
        yield eng
