from __future__ import annotations

import random
from collections.abc import Iterator

import pymupdf
import pytest

from squidpdf.core import open_pdf
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from tests.conftest import EMBEDDED_PAGE
from tests.helpers import assert_close

_SAMPLE_FACES = ("Carlito Regular", "Liberation Serif Bold", "Noto Sans Italic")
# 中: no face we ship has it, so MuPDF measures it in a font of its own.
_LETTERS = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789.,;:'\"-éàü€中"
_STRINGS = 100
_SHORTEST, _LONGEST = 1, 80
_SIZE = 11.0
_WITHIN = 0.01


@pytest.fixture(scope="module")
def driver(pdf) -> Iterator[PdfDriver]:
    with open_pdf(pdf) as engine:
        yield engine.driver


def _assert_widths_match(font: FontProgram, font_file: bytes, seed: str) -> None:
    reference = pymupdf.Font(fontbuffer=font_file)
    choose = random.Random(seed)
    for _ in range(_STRINGS):
        length = choose.randint(_SHORTEST, _LONGEST)
        text = "".join(choose.choice(_LETTERS) for _ in range(length))
        expected = reference.text_length(text, fontsize=_SIZE)
        assert_close(font.width(text, _SIZE), expected, _WITHIN, repr(text))


@pytest.mark.parametrize("name", _SAMPLE_FACES)
def test_a_shipped_faces_width_is_what_mupdf_measures(driver, name):
    face = FACES[name]
    _assert_widths_match(driver.face_font(face), face_bytes(face), name)


def test_a_trimmed_fonts_width_is_what_mupdf_measures(driver, pdf):
    doc = pymupdf.open(pdf)
    [font_object] = [font_object for font_object, *_ in doc[EMBEDDED_PAGE].get_fonts()]
    _name, _ext, _kind, font_file = doc.extract_font(font_object)
    doc.close()
    _assert_widths_match(driver.open_font(font_file), font_file, "trimmed")
