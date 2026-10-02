"""What the driver promises the engine, whatever library is underneath."""

from __future__ import annotations

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
