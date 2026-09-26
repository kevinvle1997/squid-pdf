"""PdfFile: what MuPDF's low-level API says about a file, in plain typed shapes."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core.pdf import PdfFile
from squidpdf.core.types import FontCode, Rect
from tests.helpers import assert_between, assert_equal

_TOLERANCE_PT = 0.01
_CONTENT_ORIGIN = (172.0, 700.0)  # where the fixtures' content stream starts its text


def _open(path: str) -> PdfFile:
    return PdfFile(pymupdf.open(path))


def test_text_lines_are_named_pieces(coded):
    [[piece]] = _open(coded).text_lines(0)
    assert_equal((piece.text, piece.font, piece.size), ("ABBA", "Coded", 12.0), "the piece")
    assert_equal(piece.color, (0.0, 0.0, 0.0), "its colour, r g b from 0 to 1")


@pytest.mark.parametrize(
    ("fixture", "kind", "encoding"),
    [("coded_symbol", "TrueType", ""), ("coded_type0", "Type0", "Identity-H")],
)
def test_fonts_describe_each_font_on_the_page(request, fixture, kind, encoding):
    [font] = _open(request.getfixturevalue(fixture)).fonts(0)
    assert_equal(
        (font.name, font.kind, font.encoding, font.resource, font.file_type),
        ("Coded", kind, encoding, "F1", "ttf"),
        "the page's one font",
    )
    assert_equal((font.is_embedded, font.in_form), (True, False), "embedded, on the page")


@pytest.mark.parametrize(
    ("fixture", "code_bytes", "first_code"),
    [("coded_symbol", 1, 0x20), ("coded_type0", 2, 0x01)],
)
def test_font_codes_list_what_each_code_draws(request, fixture, code_bytes, first_code):
    """Letter from ToUnicode, glyph through the font's encoding, width from /Widths or /W."""
    pdf = _open(request.getfixturevalue(fixture))
    [font] = pdf.fonts(0)
    expected = [
        FontCode(first_code + i, letter, glyph=i + 1, width=width)
        for i, (letter, width) in enumerate([("A", 500), ("B", 550), (" ", 250), ("C", 600)])
    ]
    assert_equal(pdf.font_codes(font.xref, code_bytes), expected, "codes, lowest first")


def test_font_codes_are_none_without_a_to_unicode(symbolic):
    pdf = _open(symbolic)
    [font] = pdf.fonts(0)
    assert_equal(pdf.font_codes(font.xref, 1), None, "codes of a font with no ToUnicode")


def test_to_pdf_space_undoes_the_rotation_and_the_mediabox_offset(coded):
    """Both fixtures sit on a mediabox not at 0,0; the Type0 one is also turned."""
    pdf = _open(coded)
    [[piece]] = pdf.text_lines(0)
    x, y = pdf.to_pdf_space(0, piece.origin)
    miss = abs(x - _CONTENT_ORIGIN[0]) + abs(y - _CONTENT_ORIGIN[1])
    assert_between(miss, -1, _TOLERANCE_PT, "distance from where the content drew it")


def test_erase_text_then_restore_and_add_content_redraws_in_the_same_font(coded):
    pdf = _open(coded)
    [font] = pdf.fonts(0)
    [[piece]] = pdf.text_lines(0)
    pdf.erase_text(0, [piece.box])
    assert_equal(pdf.text_lines(0), [], "text left after erasing")

    pdf.restore_font(0, font.resource, font.xref)
    x, y = pdf.to_pdf_space(0, piece.origin)
    code = "21" if font.kind == "TrueType" else "0002"
    pdf.add_content(0, f"BT /{font.resource} 12 Tf 1 0 0 1 {x} {y} Tm <{code}> Tj ET".encode())
    [[drawn]] = pdf.text_lines(0)
    assert_equal((drawn.text, drawn.font), ("B", "Coded"), "what was drawn, and in what")


def test_erase_text_leaves_text_outside_the_boxes(pdf):
    """The shared sample: page 2 has two lines; erase only the first."""
    wrapped = _open(pdf)
    first, second = wrapped.text_lines(1)
    top = first[0].box
    wrapped.erase_text(1, [Rect(top.x0, top.y0, first[-1].box.x1, top.y1)])
    left = ["".join(p.text for p in line) for line in wrapped.text_lines(1)]
    assert_equal(left, ["".join(p.text for p in second)], "lines left")
