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
    [(xref, *_)] = page.get_fonts()
    _kind, descendants = doc.xref_get_key(xref, "DescendantFonts")
    inner = int(descendants.strip("[]").split()[0])
    doc.xref_set_key(inner, "W", w)
    doc.save(path)
    return str(path), xref


def test_a_width_list_cut_short_is_no_list(tmp_path):
    path, xref = _two_byte_with(tmp_path / "cut.pdf", "[32]")

    assert_equal(open_driver(path).listed_widths(xref), None, "letters read from a broken list")


def test_a_run_past_every_code_a_font_can_have_stops_there(tmp_path):
    """`0 4000000000 500` would list four billion CIDs: it lists the codes a font can have."""
    path, xref = _two_byte_with(tmp_path / "run.pdf", "[0 4000000000 500]")

    widths = open_driver(path).listed_widths(xref) or {}

    assert_at_most(len(widths), _TWO_BYTE_CODES, "letters read from one long run")
