"""A font's width list, read as the PDF writes it, or found unreadable: never a server error."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from squidpdf.core.pdf.mupdf import open_driver
from tests.conftest import POPPINS
from tests.helpers import assert_at_most, assert_equal

_TWO_BYTE_CODES = 65536  # the most CIDs a two-byte font can have


def _two_byte_with(path: Path, w: str) -> tuple[str, int]:
    """A line in a two-byte copy of Poppins, with `w` as its width list: its file and font."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="own", fontbuffer=POPPINS.read_bytes())
    page.insert_text((72, 96), "Hello there", fontname="own", fontsize=12)
    [(font_object, *_)] = page.get_fonts()
    _kind, descendants = doc.xref_get_key(font_object, "DescendantFonts")
    inner = int(descendants.strip("[]").split()[0])
    doc.xref_set_key(inner, "W", w)
    doc.save(path)
    return str(path), font_object


def test_a_width_list_cut_short_is_no_list(tmp_path):
    path, font_object = _two_byte_with(tmp_path / "cut.pdf", "[32]")

    assert_equal(
        open_driver(path).listed_widths(font_object), None, "letters read from a broken list"
    )


def test_a_run_past_every_code_a_font_can_have_stops_there(tmp_path):
    """`0 4000000000 500` would list four billion CIDs: it lists the codes a font can have."""
    path, font_object = _two_byte_with(tmp_path / "run.pdf", "[0 4000000000 500]")

    widths = open_driver(path).listed_widths(font_object) or {}

    assert_at_most(len(widths), _TWO_BYTE_CODES, "letters read from one long run")


def test_a_type0_whose_inner_font_points_nowhere_has_no_list(type0_pointing_nowhere):
    """Its inner font, or its list of them, points past the file's last object."""
    driver = open_driver(type0_pointing_nowhere)
    damaged = next(font for font in driver.fonts(0) if font.resource == "F0")

    assert_equal(driver.listed_widths(damaged.font_object), None, "letters read from no font")
