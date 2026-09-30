"""The glyph-draws-nothing trap: what a subsetted font can really render."""

from __future__ import annotations

import io
from importlib import resources

import pytest
from fontTools.ttLib import TTFont

from squidpdf.core.coverage import Coverage
from squidpdf.core.fonts import CATALOG, face_bytes
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true

_EM = 1000  # glyph advances are per 1000 em
_WIDTH_TOLERANCE_PT = 0.01  # the table rounds each advance
_NOWHERE = "中"  # a letter no face we ship draws: none of them has Chinese
_FAMILY, _STYLE = 1, 2  # a font's name table entries for its family and style


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    """A subset keeps the charset but empties unused outlines.

    pymupdf's has_glyph returns an id for those, which is how this came to be
    reported as available. Coverage asks the glyph to draw instead.
    """
    span = next(s for s in engine.index() if s.page == EMBEDDED_PAGE)
    assert_in("é", engine.missing(span, "Février"), "accented character in a Latin word")
    assert_equal(engine.missing(span, "March"), [], "an all-covered word")


def test_a_substitute_lists_what_its_look_alike_really_draws(engine):
    """The file we ship draws past Latin-1, so € and Ω are offered; 中 no face of ours has."""
    span = next(s for s in engine.index() if s.page == REFERENCED_PAGE)
    widths = engine.widths(span)
    assert_in("é", widths, "an accent in Latin-1")
    assert_in("€", widths, "a character past Latin-1")
    assert_in("Ω", widths, "a Greek letter")
    assert_not_in(_NOWHERE, widths, "a letter no face we ship draws")
    # So a fit on it is honest: what it says is missing agrees with the table.
    missing = engine.missing(span, f"Février → 2026 {_NOWHERE}")
    assert_equal(missing, [_NOWHERE], "missing from the substitute")
    assert_equal(engine.missing(span, "".join(widths)), [], "missing from what widths() lists")


def test_every_face_we_ship_is_the_file_it_names_and_draws():
    """A face is only offered if its file is in the package, is that face, and draws letters."""
    for face in CATALOG:
        buffer = face_bytes(face)
        names = TTFont(io.BytesIO(buffer))["name"]
        in_file = f"{names.getDebugName(_FAMILY)} {names.getDebugName(_STYLE)}"
        assert_equal(in_file, face.name, f"the face in {face.file}")
        assert_true(Coverage(buffer).covers("A"), f"{face.name} draws an A")
    shipped = {path.name for path in resources.files("squidpdf").joinpath("fonts").iterdir()}
    font_files = {name for name in shipped if name.endswith((".ttf", ".otf"))}
    assert_equal(font_files, {face.file for face in CATALOG}, "font files, each in the catalog")


def test_glyph_advances_agree_with_the_server_measure(engine):
    """A width the browser works out from the table must match the server's."""
    for span in engine.index():
        widths = engine.widths(span)
        word = "".join(ch for ch in "March" if ch in widths)
        from_table = sum(widths[ch] for ch in word) * span.size / _EM
        assert_equal(
            from_table,
            pytest.approx(engine.measure(span, word), abs=_WIDTH_TOLERANCE_PT),
            f"width of {word!r} in {span.font}",
        )
