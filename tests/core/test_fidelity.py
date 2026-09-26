"""Whether a span can be edited in its own font, and the one number that tracks it."""

from __future__ import annotations

import io
from collections.abc import Callable

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from squidpdf.core import Fidelity, FidelityReport, MuPDFEngine, Span, green_rate
from squidpdf.core.fonts import base14_for
from tests.helpers import assert_all, assert_between, assert_equal

_SYMBOL_OFFSET = 0xF000  # a (3,0) cmap files code c under U+F000 + c
_EM = 1000


def _symbol_only_font() -> bytes:
    """A TrueType whose one cmap is (3,0) symbol: nothing maps a Unicode character."""
    names = [".notdef", "A", "B"]
    fb = FontBuilder(_EM, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({_SYMBOL_OFFSET + ord(n): n for n in names[1:]})
    box = TTGlyphPen(None)
    box.moveTo((50, 0))
    box.lineTo((50, 700))
    box.lineTo((550, 700))
    box.lineTo((550, 0))
    box.closePath()
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": box.glyph(), "B": box.glyph()})
    fb.setupHorizontalMetrics({n: (600, 50) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Symbolic", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    cmap = fb.font["cmap"]
    cmap.tables = cmap.tables[:1]
    cmap.tables[0].platformID, cmap.tables[0].platEncID = 3, 0
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def symbolic(tmp_path_factory) -> str:
    """One line in an embedded symbol-cmap TrueType, as a ticketing system wrote it."""
    buf = _symbol_only_font()
    doc = pymupdf.open()
    page = doc.new_page()
    xref = {}
    for name in ("file", "descriptor", "font", "content"):
        xref[name] = doc.get_new_xref()
        doc.update_object(xref[name], "<<>>")
    doc.update_stream(xref["file"], buf)
    doc.update_object(
        xref["descriptor"],
        "<</Type/FontDescriptor/FontName/Symbolic/Flags 4/FontBBox[0 -200 600 800]"
        "/ItalicAngle 0/Ascent 800/Descent -200/CapHeight 700/StemV 80"
        f"/FontFile2 {xref['file']} 0 R>>",
    )
    doc.update_object(
        xref["font"],
        "<</Type/Font/Subtype/TrueType/BaseFont/Symbolic/FirstChar 65/LastChar 66"
        f"/Widths[600 600]/FontDescriptor {xref['descriptor']} 0 R>>",
    )
    doc.update_stream(xref["content"], b"BT /F1 12 Tf 72 700 Td (ABBA) Tj ET")
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {xref['font']} 0 R>>>>")
    doc.xref_set_key(page.xref, "Contents", f"{xref['content']} 0 R")
    path = tmp_path_factory.mktemp("symbolic") / "symbolic.pdf"
    doc.save(path)
    return str(path)


def _describe_report(reports: dict[str, FidelityReport]) -> Callable[[Span], str]:
    return lambda s: f"{s.text!r} -> {reports[s.id].state}"


def test_referenced_font_is_a_substitution(engine):
    """Page 1's fonts are named but not in the file, so edits cannot match."""
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    page1 = [s for s in engine.index() if s.page == 0]
    describe = _describe_report(reports)
    assert_all(page1, lambda s: reports[s.id].state is Fidelity.SUBSTITUTE, describe)
    assert_all(page1, lambda s: bool(reports[s.id].substitute), describe)


def test_embedded_font_is_exact(engine):
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    page2 = [s for s in engine.index() if s.page == 1]
    describe = _describe_report(reports)
    assert_all(page2, lambda s: reports[s.id].state is Fidelity.EXACT, describe)


def test_green_rate_is_the_share_that_keep_their_font(engine):
    rate = green_rate(engine.assess(engine.index()))
    assert_between(rate, 0.0, 1.0, "green rate on a deliberately mixed fixture")


def test_an_embedded_font_nothing_can_map_through_is_a_substitute(symbolic, tmp_path):
    """Called exact, every redraw in it came out as empty boxes."""
    out = tmp_path / "redrawn.pdf"
    with MuPDFEngine(symbolic) as eng:
        span = next(iter(eng.index()))
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, "ABBA")
        eng.save(str(out))

    assert_equal(report.state, Fidelity.SUBSTITUTE, "fidelity of a symbol-cmap span")
    drawn = pymupdf.open(out)[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    substitute = pymupdf.Font(base14_for(span.font)).name
    assert_equal(drawn["font"], substitute, "the font that redrew it")
