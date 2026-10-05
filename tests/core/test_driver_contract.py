"""What the driver promises the engine, whatever library is underneath."""

from __future__ import annotations

import random

import pymupdf
import pytest

from squidpdf.core import Message, Rect, open_pdf
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.types import TextRun
from tests.conftest import FORM_FIELD_VALUE, FORM_LINE
from tests.helpers import assert_equal

_ADDED_AT = (72.0, 400.0)  # clear of the sample's own lines
_BLACK = (0.0, 0.0, 0.0)


def test_empty_bytes_are_not_a_font_the_driver_opens(pdf):
    """MuPDF opened Noto Serif for them, so an empty cache file measured as a real font."""
    with open_pdf(pdf) as engine:
        driver = engine.driver
        with pytest.raises(DriverError) as raised:
            driver.open_font(b"")

    assert_equal(raised.value.reason, Message("font_unreadable"), "why it can't be used")


def test_a_font_added_to_a_page_keeps_its_name_when_its_text_is_erased(pdf):
    """MuPDF drops a font no text uses any more; the engine keeps drawing by that name."""
    with open_pdf(pdf) as engine:
        driver = engine.driver
        added = driver.add_font(0, face_bytes(FACES["Carlito Regular"]), resource="S1")
        run = TextRun("Added", _ADDED_AT, added.resource)
        driver.write_text(
            0, runs=[run], size=12, color=_BLACK, opacity=1, scale_x=1, turn_ccw=0
        )
        x, y = _ADDED_AT
        driver.erase_text(0, [Rect(x, y - 12, x + 60, y)])
        left = driver.text_in(0, [Rect(x, y - 12, x + 60, y)])
        fonts = {font.resource: font.xref for font in driver.fonts(0)}

    assert_equal(left, [""], "the added text, after the erase")
    # .get: the check is whether the name is still there.
    assert_equal(fonts.get(added.resource), added.xref, "the font under its resource name")


def test_an_erase_says_what_it_could_not_clear_as_a_form_fields_value(form, tmp_path):
    """The field draws its value, not the page: the engine learns so without reading again."""
    path = tmp_path / "form.pdf"
    path.write_bytes(form)
    with open_pdf(str(path)) as engine:
        driver = engine.driver
        boxes = {piece.text: piece.box for [piece] in driver.text_lines(0)}  # one piece each
        left = driver.erase_text(0, [boxes[FORM_LINE], boxes[FORM_FIELD_VALUE]])

    assert_equal(left, ["", FORM_FIELD_VALUE], "the letters left in each box")


def _dense_page() -> bytes:
    """A page of many lines in three sizes, every seventh drawn turned."""
    doc = pymupdf.open()
    page = doc.new_page()
    for row in range(48):
        size = (7, 9, 11)[row % 3]
        # Every seventh line is drawn turned, so its letters stack down the page.
        turned = 90 if row % 7 == 3 else 0
        page.insert_text(
            (40 + (row % 5) * 6, 30 + row * 16), _LINE_TEXT, fontsize=size, rotate=turned
        )
    return doc.tobytes()


_LINE_TEXT = "Delivery 14 March, invoice 0071, total 1,284.50 EUR"


def _scanned(raw: dict, box: Rect) -> str:
    """Every letter on the page checked against `box`, in reading order: the slow way."""
    inside = []
    for block in raw["blocks"]:
        for line in block["lines"]:
            for piece in line["spans"]:
                for char in piece["chars"]:
                    x0, y0, x1, y1 = char["bbox"]
                    x, y = (x0 + x1) / 2, (y0 + y1) / 2
                    if box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1:
                        inside.append(char["c"])
    return "".join(inside)


def _boxes_on(raw: dict, chooser: random.Random) -> list[Rect]:
    """Every line's box, and boxes cut across lines whose edges sit on a letter's middle."""
    lines = [line for block in raw["blocks"] for line in block["lines"]]
    boxes = [Rect(*line["bbox"]) for line in lines]
    middles = [
        ((c["bbox"][0] + c["bbox"][2]) / 2, (c["bbox"][1] + c["bbox"][3]) / 2)
        for line in lines
        for piece in line["spans"]
        for c in piece["chars"]
    ]
    for _ in range(200):
        (xa, ya), (xb, yb) = chooser.sample(middles, 2)
        boxes.append(Rect(min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)))
    return boxes


@pytest.mark.parametrize("seed", range(4))
def test_a_boxs_letters_are_those_a_check_of_every_letter_finds(tmp_path, seed):
    """Found by halving down the page, they're the same letters, in the same order.

    The erase and the redaction check both read them: a letter missed is text left.
    """
    path = tmp_path / "dense.pdf"
    path.write_bytes(_dense_page())
    with pymupdf.open(path) as doc:
        raw = doc[0].get_text("rawdict")
    boxes = _boxes_on(raw, random.Random(seed))

    with open_pdf(str(path)) as engine:
        read = engine.driver.text_in(0, boxes)

    assert_equal(read, [_scanned(raw, box) for box in boxes], "each box's letters")
