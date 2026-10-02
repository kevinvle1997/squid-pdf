"""The glyph-draws-nothing trap: what a subsetted font can really render."""

from __future__ import annotations

import io
import unicodedata
from importlib import resources

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import cmap_format_12
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

from squidpdf.core import open_pdf
from squidpdf.core.fonts.catalog import CATALOG, FACES, face_bytes
from squidpdf.core.fonts.coverage import coverage_of
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true

_EM = 1000  # glyph advances are per 1000 em
_WIDTH_TOLERANCE_PT = 0.01  # the table rounds each advance
_NOWHERE = "中"  # a letter no face we ship draws: none of them has Chinese
_FAMILY, _STYLE = 1, 2  # a font's name table entries for its family and style
_PAST_UNICODE = 0x110041  # one past U+10FFFF, the last code point, plus "A"


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    """A subset keeps the charset but empties unused outlines.

    pymupdf's has_glyph returns an id for those, which is how this came to be
    reported as available. Coverage asks the glyph to draw instead.
    """
    span = next(s for s in engine.index() if s.page == EMBEDDED_PAGE)
    assert_in(
        "é", engine.plan_for(span, "Février").missing, "accented character in a Latin word"
    )
    assert_equal(engine.plan_for(span, "March").missing, [], "an all-covered word")


def test_a_substitute_lists_what_its_look_alike_really_draws(engine):
    """The file we ship draws past Latin-1, so € and Ω are offered; 中 no face of ours has."""
    span = next(s for s in engine.index() if s.page == REFERENCED_PAGE)
    widths = engine.widths(span)
    assert_in("é", widths, "an accent in Latin-1")
    assert_in("€", widths, "a character past Latin-1")
    assert_in("Ω", widths, "a Greek letter")
    assert_not_in(_NOWHERE, widths, "a letter no face we ship draws")
    # So a fit on it is honest: what it says is missing agrees with the table.
    missing = engine.plan_for(span, f"Février → 2026 {_NOWHERE}").missing
    assert_equal(missing, [_NOWHERE], "missing from the substitute")
    assert_equal(
        engine.plan_for(span, "".join(widths)).missing, [], "missing from what widths() lists"
    )


def test_every_face_we_ship_is_the_file_it_names_and_draws():
    """A face is only offered if its file is in the package, is that face, and draws letters."""
    for face in CATALOG:
        buffer = face_bytes(face)
        names = TTFont(io.BytesIO(buffer))["name"]
        in_file = f"{names.getDebugName(_FAMILY)} {names.getDebugName(_STYLE)}"
        assert_equal(in_file, face.name, f"the face in {face.file}")
        assert_true(coverage_of(buffer).covers("A"), f"{face.name} draws an A")
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


def test_a_ligature_a_stored_font_draws_counts_as_drawn(tmp_path):
    """Typeset text keeps ﬁ as one letter, U+FB01; trimmed Times draws it, by its shape "fi"."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 100), "The ﬁnancial year", fontname="emb", fontsize=12)
    doc.subset_fonts(verbose=False)
    path = str(tmp_path / "ligature.pdf")
    doc.save(path)

    with open_pdf(path) as engine:
        [span] = engine.index()
        assert_equal(engine.plan_for(span, span.text).missing, [], "letters the font lacks")


def _built_of_parts(*, parts_drawn: bool) -> bytes:
    """A font whose Á is built from its A and its acute, as fonts build accented letters.

    A trimmed font can keep Á while emptying the A and the acute it's built
    from: then Á draws nothing, though it still points at its parts.
    """
    builder = FontBuilder(_EM, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A", "acute", "Aacute"])
    builder.setupCharacterMap({ord("A"): "A", 0xB4: "acute", ord("Á"): "Aacute"})
    pen = TTGlyphPen(None)
    if parts_drawn:
        pen.moveTo((50, 0))
        pen.lineTo((50, 700))
        pen.lineTo((550, 0))
        pen.closePath()
    part = pen.glyph()
    built = Glyph()
    built.numberOfContours = -1  # a glyph made of others, not of its own outline
    built.components = []
    for name in ("A", "acute"):
        component = GlyphComponent()
        component.glyphName, component.x, component.y, component.flags = name, 0, 0, 0
        built.components.append(component)
    empty = TTGlyphPen(None).glyph()
    builder.setupGlyf({".notdef": empty, "A": part, "acute": part, "Aacute": built})
    builder.setupHorizontalMetrics({name: (600, 0) for name in builder.font.getGlyphOrder()})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Built", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    font_file = io.BytesIO()
    builder.save(font_file)
    return font_file.getvalue()


@pytest.mark.parametrize("parts_drawn", [True, False], ids=["parts kept", "parts emptied"])
def test_a_letter_built_from_other_shapes_draws_only_if_they_do(parts_drawn):
    coverage = coverage_of(_built_of_parts(parts_drawn=parts_drawn))
    assert_equal(coverage.covers("Á"), parts_drawn, "whether Á draws")


def test_a_letter_table_past_unicode_is_passed_over_not_a_crash():
    """A broken font can map a code past U+10FFFF, the last there is: no letter is there."""
    font = TTFont(io.BytesIO(face_bytes(FACES["Liberation Sans Regular"])))
    table = cmap_format_12(12)
    table.platformID, table.platEncID, table.language = 3, 10, 0  # Windows, full Unicode
    table.cmap = {ord("A"): "A", _PAST_UNICODE: "A"}  # "A" is the A's shape's name
    font["cmap"].tables = [table]
    font_file = io.BytesIO()
    font.save(font_file)

    coverage = coverage_of(font_file.getvalue())
    assert_equal(coverage.drawable(), [" ", "A"], "what it draws")


def test_a_letter_typed_in_two_pieces_is_the_one_the_font_has(tmp_path):
    """é can be one letter, or e and an accent (as macOS pastes it); the font keeps one."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 100), "Café in Zürich", fontname="emb", fontsize=12)
    doc.subset_fonts(verbose=False)  # keeps é and ü whole, not their pieces
    path = str(tmp_path / "accented.pdf")
    doc.save(path)
    in_pieces = unicodedata.normalize("NFD", "Café in Zürich")

    with open_pdf(path) as engine:
        [span] = engine.index()
        assert_equal(engine.plan_for(span, in_pieces).missing, [], "letters the font lacks")
        assert_equal(engine.plan_for(span, in_pieces).left_out, [], "letters left out")


def test_a_font_coverage_cant_read_draws_what_the_library_lists():
    """Bytes no parser reads (a Type 1 font, say): the library's own list is the best left."""
    coverage = coverage_of(b"not a font program", listed_letters=[ord("A")])
    assert_equal((coverage.covers("A"), coverage.covers("B")), (True, False), "A, then B")
