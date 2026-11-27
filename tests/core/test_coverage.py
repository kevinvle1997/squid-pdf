from __future__ import annotations

import io
import json
import unicodedata
from importlib import resources

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import cmap_format_12  # noqa: PLC2701 (fontTools names its table modules so)
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent  # noqa: PLC2701 (fontTools names its table modules so)

from squidpdf.core import open_pdf
from squidpdf.core.fonts.catalog import CATALOG, FACES, face_bytes
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.look_alike import broadest
from squidpdf.core.fonts.substitute import face_coverage
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.core.conftest import finished_font
from tests.helpers import assert_close, assert_equal, assert_in, assert_not_in, assert_true

_EM = 1000
_WIDTH_TOLERANCE_PT = 0.01
_NOWHERE = "中"
_FAMILY, _STYLE = 1, 2
_PAST_UNICODE = 0x110041  # one past U+10FFFF, the last code point, plus "A"


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    span = next(s for s in engine.index() if s.page == EMBEDDED_PAGE)
    assert_in(
        "é", engine.fit.plan_for(span, "Février").missing, "accented character in a Latin word"
    )
    assert_equal(engine.fit.plan_for(span, "March").missing, [], "an all-covered word")


def test_a_substitute_lists_what_its_look_alike_really_draws(engine):
    span = next(s for s in engine.index() if s.page == REFERENCED_PAGE)
    widths = engine.widths(span)
    assert_in("é", widths, "an accent in Latin-1")
    assert_in("€", widths, "a character past Latin-1")
    assert_in("Ω", widths, "a Greek letter")
    assert_not_in(_NOWHERE, widths, "a letter no face we ship draws")
    missing = engine.fit.plan_for(span, f"Février → 2026 {_NOWHERE}").missing
    assert_equal(missing, [_NOWHERE], "missing from the substitute")
    assert_equal(
        engine.fit.plan_for(span, "".join(widths)).missing,
        [],
        "missing from what widths() lists",
    )


def test_every_face_we_ship_is_the_file_it_names_and_draws():
    for face in CATALOG:
        buffer = face_bytes(face)
        names = TTFont(io.BytesIO(buffer))["name"]
        in_file = f"{names.getDebugName(_FAMILY)} {names.getDebugName(_STYLE)}"
        assert_equal(in_file, face.name, f"the face in {face.file}")
        assert_true(coverage_of(buffer).covers("A"), f"{face.name} draws an A")
    shipped = {path.name for path in resources.files("squidpdf").joinpath("fonts").iterdir()}
    font_files = {name for name in shipped if name.endswith((".ttf", ".otf"))}
    assert_equal(font_files, {face.file for face in CATALOG}, "font files, each in the catalog")


def test_the_browser_finds_every_face_we_ship_by_its_name():
    listed = resources.files("squidpdf").joinpath("fonts", "face-files.json").read_text("utf-8")
    assert_equal(
        json.loads(listed),
        {face.name: face.file for face in CATALOG},
        "each face's file, by name (rebuild: uv run python scripts/face_files.py)",
    )


def test_the_browser_knows_which_letters_each_face_draws_as_the_server_does():
    listed = (
        resources.files("squidpdf").joinpath("fonts", "face-letters.json").read_text("utf-8")
    )
    faces = json.loads(listed)
    rebuild = "(rebuild: uv run python scripts/face_files.py)"
    assert_equal(list(faces), [face.name for face in CATALOG], f"each face, in order {rebuild}")
    for face in CATALOG:
        drawn = {ord(ch) for ch in face_coverage(face).drawable()}
        assert_equal(
            _codes_in(faces[face.name]["letters"]), drawn, f"what {face.name} draws {rebuild}"
        )
        said = faces[face.name]["broadest"]
        assert_equal(said, broadest(face).name, f"where {face.name}'s line goes {rebuild}")


def _codes_in(runs: list[int | list[int]]) -> set[int]:
    codes: set[int] = set()
    for run in runs:
        first, last = (run, run) if isinstance(run, int) else run
        codes.update(range(first, last + 1))
    return codes


def test_glyph_advances_agree_with_the_server_measure(engine):
    for span in engine.index():
        widths = engine.widths(span)
        word = "".join(ch for ch in "March" if ch in widths)
        from_table = sum(widths[ch] for ch in word) * span.size / _EM
        assert_close(
            from_table,
            engine.fit.measure(span, word),
            _WIDTH_TOLERANCE_PT,
            f"width of {word!r} in {span.font}",
        )


def test_a_ligature_a_stored_font_draws_counts_as_drawn(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 100), "The ﬁnancial year", fontname="emb", fontsize=12)
    doc.subset_fonts(verbose=False)
    path = str(tmp_path / "ligature.pdf")
    doc.save(path)

    with open_pdf(path) as engine:
        [span] = engine.index()
        assert_equal(engine.fit.plan_for(span, span.text).missing, [], "letters the font lacks")


def _built_of_parts(*, parts_drawn: bool) -> bytes:
    """A trimmed font can keep Á but empty the A and acute it's built from: it draws nothing."""
    builder = FontBuilder(_EM, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A", "acute", "Aacute"])
    builder.setupCharacterMap({ord("A"): "A", 0xB4: "acute", ord("Á"): "Aacute"})
    empty = TTGlyphPen(None).glyph()
    part = _triangle() if parts_drawn else empty
    built = Glyph()
    built.numberOfContours = -1
    built.components = []
    for name in ("A", "acute"):
        component = GlyphComponent()
        component.glyphName, component.x, component.y, component.flags = name, 0, 0, 0
        built.components.append(component)
    builder.setupGlyf({".notdef": empty, "A": part, "acute": part, "Aacute": built})
    builder.setupHorizontalMetrics({name: (600, 0) for name in builder.font.getGlyphOrder()})
    return finished_font(builder, family="Built")


def _triangle() -> Glyph:
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((550, 0))
    pen.closePath()
    return pen.glyph()


@pytest.mark.parametrize("parts_drawn", [True, False], ids=["parts kept", "parts emptied"])
def test_a_letter_built_from_other_shapes_draws_only_if_they_do(parts_drawn):
    coverage = coverage_of(_built_of_parts(parts_drawn=parts_drawn))
    assert_equal(coverage.covers("Á"), parts_drawn, "whether Á draws")


def test_a_letter_table_past_unicode_is_passed_over_not_a_crash():
    font = TTFont(io.BytesIO(face_bytes(FACES["Liberation Sans Regular"])))
    table = cmap_format_12(12)
    table.platformID, table.platEncID, table.language = 3, 10, 0
    table.cmap = {ord("A"): "A", _PAST_UNICODE: "A"}
    font["cmap"].tables = [table]
    font_file = io.BytesIO()
    font.save(font_file)

    coverage = coverage_of(font_file.getvalue())
    assert_equal(coverage.drawable(), [" ", "A"], "what it draws")


def test_a_letter_typed_in_two_pieces_is_the_one_the_font_has(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 100), "Café in Zürich", fontname="emb", fontsize=12)
    doc.subset_fonts(verbose=False)
    path = str(tmp_path / "accented.pdf")
    doc.save(path)
    in_pieces = unicodedata.normalize("NFD", "Café in Zürich")

    with open_pdf(path) as engine:
        [span] = engine.index()
        assert_equal(engine.fit.plan_for(span, in_pieces).missing, [], "letters the font lacks")
        assert_equal(engine.fit.plan_for(span, in_pieces).left_out, [], "letters left out")


def test_a_font_coverage_cant_read_draws_what_the_library_lists():
    coverage = coverage_of(b"not a font program", listed_letters=[ord("A")])
    assert_equal((coverage.covers("A"), coverage.covers("B")), (True, False), "A, then B")
