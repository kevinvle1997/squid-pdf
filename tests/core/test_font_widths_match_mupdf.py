"""A font's width for a string is MuPDF's own, however we add it up."""

from __future__ import annotations

import random
from collections.abc import Iterator

import pymupdf
import pytest

from squidpdf.core.driver import FontProgram
from squidpdf.core.fonts import FACES, face_bytes
from squidpdf.core.mupdf import MuPDFDriver
from tests.conftest import EMBEDDED_PAGE
from tests.helpers import assert_close

_SAMPLE_FACES = ("Carlito Regular", "Liberation Serif Bold", "Noto Sans Italic")
# 中: no face we ship has it, so MuPDF measures it in a font of its own.
_LETTERS = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789.,;:'\"-éàü€中"
_STRINGS = 100
_SHORTEST, _LONGEST = 1, 80  # letters in a sample string
_SIZE = 11.0  # points
_WITHIN = 0.01  # points: far below what a fit check could notice


@pytest.fixture(scope="module")
def driver(pdf) -> Iterator[MuPDFDriver]:
    """The sample, open in the driver, which opens fonts to measure with."""
    driver = MuPDFDriver(pdf)
    yield driver
    driver.close()


def _assert_widths_match(font: FontProgram, font_file: bytes, seed: str) -> None:
    """`font`'s width for many strings is what MuPDF's `text_length` gives for them."""
    reference = pymupdf.Font(fontbuffer=font_file)
    choose = random.Random(seed)
    for _ in range(_STRINGS):
        length = choose.randint(_SHORTEST, _LONGEST)
        text = "".join(choose.choice(_LETTERS) for _ in range(length))
        expected = reference.text_length(text, fontsize=_SIZE)
        assert_close(font.width(text, _SIZE), expected, _WITHIN, repr(text))


@pytest.mark.parametrize("name", _SAMPLE_FACES)
def test_a_shipped_faces_width_is_what_mupdf_measures(driver, name):
    """Each face we ship, measured the way the fit check measures it."""
    face = FACES[name]
    _assert_widths_match(driver.face_font(face), face_bytes(face), name)


def test_a_trimmed_fonts_width_is_what_mupdf_measures(driver, pdf):
    """The sample's own font, trimmed to its letters: most of these it has no shape for."""
    doc = pymupdf.open(pdf)
    [xref] = [xref for xref, *_ in doc[EMBEDDED_PAGE].get_fonts()]
    _name, _ext, _kind, font_file = doc.extract_font(xref)
    doc.close()
    _assert_widths_match(driver.open_font(font_file), font_file, "trimmed")
