"""The glyph-draws-nothing trap: what a subsetted font can really render."""

from __future__ import annotations

import io

import pytest
from fontTools.ttLib import TTFont

from squidpdf.core.coverage import Coverage, _glyph_name_for_each_letter
from squidpdf.core.types import Codepoint, GlyphId
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.core.conftest import _truetype
from tests.helpers import assert_equal, assert_false, assert_in, assert_not_in, assert_true

_EM = 1000  # glyph advances are per 1000 em
_WIDTH_TOLERANCE_PT = 0.01  # the table rounds each advance


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    """A subset keeps the charset but empties unused outlines.

    pymupdf's has_glyph returns an id for those, which is how this came to be
    reported as available. Coverage asks the glyph to draw instead.
    """
    span = next(s for s in engine.index() if s.page == EMBEDDED_PAGE)
    assert_in("é", engine.missing(span, "Février"), "accented character in a Latin word")
    assert_equal(engine.missing(span, "March"), [], "an all-covered word")


def test_a_font_coverage_cant_read_draws_what_mupdf_claims():
    """It used to claim everything, and a symbol-only cmap redrew as boxes."""
    cov = Coverage(b"", claimed=[ord("x")])  # no bytes: a font program it can't read
    assert_true(cov.covers(" "), "whitespace is always drawable")
    assert_true(cov.covers("x"), "a character MuPDF claims")
    assert_false(cov.covers("y"), "a character nothing claims")


def test_glyphs_leave_out_what_a_subset_emptied(engine):
    """The browser's live check reads this table, so it must not promise é."""
    span = next(s for s in engine.index() if s.page == EMBEDDED_PAGE)
    glyphs = engine.glyphs(span)
    assert_in("M", glyphs, "a letter the page uses")
    assert_not_in("é", glyphs, "an accent the subset emptied")


def test_a_substitute_draws_nothing_past_latin_1(engine):
    """The base-14 stand-in turns € and — into a dot, so neither is offered."""
    span = next(s for s in engine.index() if s.page == REFERENCED_PAGE)
    glyphs = engine.glyphs(span)
    assert_in("é", glyphs, "an accent in Latin-1")
    assert_not_in("€", glyphs, "a character past Latin-1")
    assert_not_in("—", glyphs, "a character past Latin-1")


def test_glyph_advances_agree_with_the_server_measure(engine):
    """A width the browser works out from the table must match the server's."""
    for span in engine.index():
        glyphs = engine.glyphs(span)
        word = "".join(ch for ch in "March" if ch in glyphs)
        from_table = sum(glyphs[ch] for ch in word) * span.size / _EM
        assert_equal(
            from_table,
            pytest.approx(engine.measure(span, word), abs=_WIDTH_TOLERANCE_PT),
            f"width of {word!r} in {span.font}",
        )


def test_a_substitute_reports_what_it_cannot_draw_as_missing(engine):
    """So a fit on a substitute span is honest, and agrees with the glyph table."""
    span = next(s for s in engine.index() if s.page == REFERENCED_PAGE)
    assert_equal(engine.missing(span, "Février → 2026"), ["→"], "missing from the substitute")
    drawable = "".join(engine.glyphs(span))
    assert_equal(engine.missing(span, drawable), [], "missing from what glyphs() lists")


def test_glyph_names_come_from_the_ids_given_when_the_font_has_no_letter_table():
    """The fixture font has no cmap: the ids a letter list gave name each letter's shape."""
    font = TTFont(io.BytesIO(_truetype(None)))
    glyph_ids = {"A": GlyphId(1), "B": GlyphId(2), " ": GlyphId(3), "Z": GlyphId(99)}
    expected = {
        Codepoint(ord("A")): "A",
        Codepoint(ord("B")): "B",
        Codepoint(ord(" ")): "space",
    }
    assert_equal(
        _glyph_name_for_each_letter(font, glyph_ids),
        expected,
        "each letter's shape, Z past the end",
    )
