from __future__ import annotations

import random
from collections.abc import Iterator, Sequence

import pymupdf
import pytest

from squidpdf.core import Message, Rect, open_pdf
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.types import TextRun
from tests.conftest import FORM_FIELD_VALUE, FORM_LINE
from tests.helpers import assert_equal

_ADDED_AT = (72.0, 400.0)
_BLACK = (0.0, 0.0, 0.0)
_TEXT_FIELD = 7  # PyMuPDF's PDF_WIDGET_TYPE_TEXT, set at import, so type checkers can't see it
# Two fonts whose names differ only past the 31 bytes MuPDF keeps: an upright and its italic.
_CUT_ALIKE = ("ABCDEF+MyriadProX-SemiboldCondensed", "GHIJKL+MyriadProX-SemiboldCondensedIt")


def _named_font(doc: pymupdf.Document, name: str) -> int:
    font = doc.get_new_xref()
    widths = " ".join(["500"] * 95)
    doc.update_object(
        font,
        f"<</Type/Font/Subtype/TrueType/BaseFont/{name}/FirstChar 32/LastChar 126"
        f"/Widths[{widths}]/Encoding/WinAnsiEncoding>>",
    )
    return font


def test_empty_bytes_are_not_a_font_the_driver_opens(pdf):
    with open_pdf(pdf) as engine:
        driver = engine.driver
        with pytest.raises(DriverError) as raised:
            driver.open_font(b"")

    assert_equal(raised.value.reason, Message("font_unreadable"), "why it can't be used")


def test_a_font_added_to_a_page_keeps_its_name_when_its_text_is_erased(pdf):
    with open_pdf(pdf) as engine:
        driver = engine.driver
        added = driver.add_font(0, face_bytes(FACES["Carlito Regular"]), resource="S1")
        run = TextRun("Added", _ADDED_AT, added.resource)
        driver.write_text(
            0,
            runs=[run],
            size=12,
            color=_BLACK,
            opacity=1,
            scale_x=1,
            turn_ccw=0,
            letter_spacing=0,
        )
        x, y = _ADDED_AT
        driver.erase_text(0, [Rect(x, y - 12, x + 60, y)])
        left = driver.text_in(0, [Rect(x, y - 12, x + 60, y)])
        fonts = {font.resource: font.font_object for font in driver.fonts(0)}

    assert_equal(left, [""], "the added text, after the erase")
    assert_equal(
        fonts.get(added.resource), added.font_object, "the font under its resource name"
    )


def test_an_erase_says_what_it_could_not_clear_as_a_form_fields_value(form, tmp_path):
    path = tmp_path / "form.pdf"
    path.write_bytes(form)
    with open_pdf(str(path)) as engine:
        driver = engine.driver
        boxes = {piece.text: piece.box for [piece] in driver.text_lines(0)}
        left = driver.erase_text(0, [boxes[FORM_LINE], boxes[FORM_FIELD_VALUE]])

    assert_equal(left, ["", FORM_FIELD_VALUE], "the letters left in each box")


def _dense_page() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for row in range(48):
        size = (7, 9, 11)[row % 3]
        turned = 90 if row % 7 == 3 else 0
        page.insert_text(
            (40 + (row % 5) * 6, 30 + row * 16), _LINE_TEXT, fontsize=size, rotate=turned
        )
    courier = page.insert_font(fontname="cour")
    page.insert_text((39.5, _LAST_BASELINE), _NO_WIDTH, fontname="cour", fontsize=11)
    page.insert_text((40, _LAST_BASELINE), _LINE_TEXT, fontname="cour", fontsize=11)
    widths = " ".join("0" if code == ord(_NO_WIDTH) else "600" for code in _LISTED)
    doc.xref_set_key(courier, "FirstChar", str(_LISTED.start))
    doc.xref_set_key(courier, "LastChar", str(_LISTED.stop - 1))
    doc.xref_set_key(courier, "Widths", f"[{widths}]")
    return doc.tobytes()


_LINE_TEXT = "Delivery 14 March, invoice 0071, total 1,284.50 EUR"
_LAST_BASELINE = 30 + 48 * 16
_NO_WIDTH = "|"
_LISTED = range(0x20, 0x7F)


def _scanned(raw: dict, box: Rect) -> str:
    inside = []
    for char, _line in _each_letter_in_its_line(raw):
        x, y = _middle_of(char["bbox"])
        if box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1:
            inside.append(char["c"])
    return "".join(inside)


def _each_letter_in_its_line(raw: dict) -> Iterator[tuple[dict, dict]]:
    for block in raw["blocks"]:
        for line in block["lines"]:
            for piece in line["spans"]:
                for char in piece["chars"]:
                    yield char, line


def _middle_of(bbox: Sequence[float]) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def _holds(outer: Sequence[float], inner: Sequence[float]) -> bool:
    outer_x0, outer_y0, outer_x1, outer_y1 = outer
    inner_x0, inner_y0, inner_x1, inner_y1 = inner
    return (
        outer_x0 <= inner_x0
        and outer_y0 <= inner_y0
        and inner_x1 <= outer_x1
        and inner_y1 <= outer_y1
    )


def _boxes_on(raw: dict, chooser: random.Random) -> list[Rect]:
    boxes = [Rect(*line["bbox"]) for block in raw["blocks"] for line in block["lines"]]
    middles = [_middle_of(char["bbox"]) for char, _line in _each_letter_in_its_line(raw)]
    for _ in range(200):
        (xa, ya), (xb, yb) = chooser.sample(middles, 2)
        boxes.append(Rect(min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)))
    return boxes


@pytest.mark.parametrize("seed", range(4))
def test_a_boxs_letters_are_those_a_check_of_every_letter_finds(tmp_path, seed):
    path = tmp_path / "dense.pdf"
    path.write_bytes(_dense_page())
    with pymupdf.open(path) as doc:
        raw = doc[0].get_text("rawdict")
    boxes = _boxes_on(raw, random.Random(seed))

    with open_pdf(str(path)) as engine:
        read = engine.driver.text_in(0, boxes)

    assert_equal(read, [_scanned(raw, box) for box in boxes], "each box's letters")


def test_a_lines_box_holds_each_of_its_letters_boxes(tmp_path):
    path = tmp_path / "dense.pdf"
    path.write_bytes(_dense_page())
    with pymupdf.open(path) as doc:
        raw = doc[0].get_text("rawdict")

    outside = [
        char["c"]
        for char, line in _each_letter_in_its_line(raw)
        if not _holds(line["bbox"], char["bbox"])
    ]
    assert_equal(outside, [], "letters outside their line's box")


def test_each_piece_names_a_font_the_page_lists_or_none_for_a_form_fields_own(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), FORM_LINE, fontname="tiro", fontsize=12)
    field = pymupdf.Widget()
    field.field_type = _TEXT_FIELD
    field.field_name = "ssn"
    field.field_value = FORM_FIELD_VALUE
    field.rect = pymupdf.Rect(72, 120, 300, 140)
    page.add_widget(field)
    path = tmp_path / "field.pdf"
    doc.save(path)
    with open_pdf(str(path)) as engine:
        listed = {font.font_object for font in engine.driver.fonts(0)}
        pieces = [piece for line in engine.driver.text_lines(0) for piece in line]

    named_by = {piece.text: piece.font_object for piece in pieces}
    [times] = listed
    assert_equal(named_by, {FORM_LINE: times, FORM_FIELD_VALUE: None}, "each piece's font")


def test_two_fonts_whose_names_mupdf_cuts_alike_are_neither_taken_for_the_other(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    fonts = [_named_font(doc, name) for name in _CUT_ALIKE]
    content = doc.get_new_xref()
    doc.update_object(content, "<<>>")
    doc.update_stream(
        content, b"BT /F1 14 Tf 72 700 Td (Upright) Tj ET BT /F2 14 Tf 72 650 Td (Italic) Tj ET"
    )
    doc.xref_set_key(
        page.xref, "Resources", f"<</Font<</F1 {fonts[0]} 0 R/F2 {fonts[1]} 0 R>>>>"
    )
    doc.xref_set_key(page.xref, "Contents", f"{content} 0 R")
    path = tmp_path / "cut.pdf"
    doc.save(path)
    with open_pdf(str(path)) as engine:
        named_by = [(span.font, span.font_object) for span in engine.index()]

    cut = "MyriadProX-SemiboldConde"
    assert_equal(
        named_by, [(cut, None), (cut, None)], "each span's font: its cut name, no object"
    )
