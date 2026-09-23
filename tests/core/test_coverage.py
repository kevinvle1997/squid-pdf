"""The glyph-draws-nothing trap: what a subsetted font can really render."""

from __future__ import annotations

from squidpdf.core.coverage import Coverage
from tests.helpers import assert_equal, assert_in, assert_true


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    """A subset keeps the charset but empties unused outlines.

    pymupdf's has_glyph returns an id for those, which is how this came to be
    reported as available. Coverage asks the glyph to draw instead.
    """
    span = next(s for s in engine.index() if s.page == 1)
    assert_in("é", engine.missing(span, "Février"), "accented character in a Latin word")
    assert_equal(engine.missing(span, "March"), [], "an all-covered word")


def test_coverage_treats_whitespace_as_drawable():
    cov = Coverage(b"")  # unparseable
    assert_true(cov.covers(" ") is True, "whitespace is always drawable")
    assert_true(cov.covers("x") is True, "never claims a problem it cannot prove")
