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
from tests.helpers import assert_all, assert_between, assert_equal, assert_true

_SYMBOL_OFFSET = 0xF000  # a (3,0) cmap files code c under U+F000 + c
_EM = 1000
_SIZE = 12
_OFFSET_BOX = "[100 50 712 842]"  # a mediabox not at 0,0
_ORIGIN_TOLERANCE_PT = 0.01

# Glyph ids: A 1, B 2, space 3, C 4. C's outline is emptied, as a subset leaves one.
_GLYPHS = [".notdef", "A", "B", "space", "C"]

# Codes numbered by first use, as Chrome writes them: 0x20 is A.
_BY_FIRST_USE = {0x20: "A", 0x21: "B", 0x22: "space", 0x23: "C"}

# Advances in the font dict, not the font program's own 600, so a test can tell them apart.
_WIDTHS = "500 550 250 600"
_ADVANCES = {"A": 500.0, "B": 550.0, " ": 250.0}

_DESCRIPTOR = (
    "<</Type/FontDescriptor/FontName/{name}/Flags 4/FontBBox[0 -200 600 800]"
    "/ItalicAngle 0/Ascent 800/Descent -200/CapHeight 700/StemV 80/FontFile2 {file}>>"
)


def _truetype(cmap: dict[int, str] | None, symbol: bool = True) -> bytes:
    """A TrueType with only this cmap, (3,0) symbol, or with no cmap table at all."""
    fb = FontBuilder(_EM, isTTF=True)
    fb.setupGlyphOrder(_GLYPHS)
    fb.setupCharacterMap(cmap or {})
    box = TTGlyphPen(None)
    box.moveTo((50, 0))
    box.lineTo((50, 700))
    box.lineTo((550, 700))
    box.lineTo((550, 0))
    box.closePath()
    outline = box.glyph()  # the pen resets on glyph(), so one outline serves both letters
    fb.setupGlyf({n: outline if n in ("A", "B") else TTGlyphPen(None).glyph() for n in _GLYPHS})
    fb.setupHorizontalMetrics({n: (600, 50) for n in _GLYPHS})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Symbolic", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    if cmap is None:
        del fb.font["cmap"]
    elif symbol:
        table = fb.font["cmap"]
        table.tables = table.tables[:1]
        table.tables[0].platformID, table.tables[0].platEncID = 3, 0
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


def _to_unicode(codespace: str, body: str) -> bytes:
    """A ToUnicode CMap: which letter each code is."""
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap"
        " /CMapName /Test def /CMapType 2 def"
        f" 1 begincodespacerange {codespace} endcodespacerange {body}"
        " endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()


def _embed_by_hand(
    path: str,
    streams: dict[str, bytes],
    dicts: dict[str, str],
    box: str | None = None,
    rotate: int = 0,
) -> str:
    """One page drawing streams["content"] with /F1 as dicts["font"].

    Each dict names another object as {its key}; the font program is {file}.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    xref = {name: doc.get_new_xref() for name in [*streams, *dicts]}
    for x in xref.values():
        doc.update_object(x, "<<>>")
    refs = {name: f"{x} 0 R" for name, x in xref.items()}
    for name, obj in dicts.items():
        doc.update_object(xref[name], obj.format(**refs))
    for name, data in streams.items():
        doc.update_stream(xref[name], data)
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {refs['font']}>>>>")
    doc.xref_set_key(page.xref, "Contents", refs["content"])
    if box is not None:
        doc.xref_set_key(page.xref, "MediaBox", box)
    if rotate:
        doc.xref_set_key(page.xref, "Rotate", str(rotate))
    doc.save(path)
    return path


@pytest.fixture(scope="module")
def symbolic(tmp_path_factory) -> str:
    """One line in an embedded symbol-cmap TrueType with no ToUnicode."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("symbolic") / "symbolic.pdf"),
        {
            "file": _truetype({_SYMBOL_OFFSET + ord(n): n for n in "AB"}),
            "content": b"BT /F1 12 Tf 72 700 Td (ABBA) Tj ET",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Symbolic"),
            "font": "<</Type/Font/Subtype/TrueType/BaseFont/Symbolic/FirstChar 65/LastChar 66"
            "/Widths[600 600]/FontDescriptor {descriptor}>>",
        },
    )


@pytest.fixture(scope="module")
def coded_symbol(tmp_path_factory) -> str:
    """ABBA in a symbol-cmap TrueType, codes by first use, with a ToUnicode, as tickets are."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("coded") / "symbol.pdf"),
        {
            "file": _truetype({_SYMBOL_OFFSET + c: n for c, n in _BY_FIRST_USE.items()}),
            "to_unicode": _to_unicode(
                "<00> <FF>",
                "1 beginbfrange <20> <21> <0041> endbfrange"
                " 2 beginbfchar <22> <0020> <23> <0043> endbfchar",
            ),
            "content": b"BT /F1 12 Tf 172 700 Td <20212120> Tj ET",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Coded"),
            "font": "<</Type/Font/Subtype/TrueType/BaseFont/Coded/FirstChar 32/LastChar 35"
            f"/Widths[{_WIDTHS}]/FontDescriptor {{descriptor}}/ToUnicode {{to_unicode}}>>",
        },
        box=_OFFSET_BOX,
    )


@pytest.fixture(scope="module")
def coded_type0(tmp_path_factory) -> str:
    """ABBA in a Type0 Identity-H TrueType with no cmap table, as the order file is, turned."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("coded") / "type0.pdf"),
        {
            "file": _truetype(None),
            "to_unicode": _to_unicode(
                "<0000> <FFFF>",
                "1 beginbfrange <0001> <0003> [<0041> <0042> <0020>] endbfrange"
                " 1 beginbfchar <0004> <0043> endbfchar",
            ),
            "content": b"BT /F1 12 Tf 172 700 Td <0001000200020001> Tj ET",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Coded"),
            "cid": "<</Type/Font/Subtype/CIDFontType2/BaseFont/Coded"
            "/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
            f"/FontDescriptor {{descriptor}}/DW 1000/W[1[{_WIDTHS}]]/CIDToGIDMap/Identity>>",
            "font": "<</Type/Font/Subtype/Type0/BaseFont/Coded/Encoding/Identity-H"
            "/DescendantFonts[{cid}]/ToUnicode {to_unicode}>>",
        },
        box=_OFFSET_BOX,
        rotate=90,
    )


@pytest.fixture(params=["coded_symbol", "coded_type0"])
def coded(request) -> str:
    """A font the file reaches only by code, in either shape."""
    return request.getfixturevalue(request.param)


def _drawn(path: str) -> list[dict]:
    """Every span of text on the saved file's first page, re-read."""
    blocks = pymupdf.open(path)[0].get_text("rawdict")["blocks"]
    return [s for b in blocks for line in b.get("lines", []) for s in line["spans"]]


def _text(span: dict) -> str:
    """A rawdict span's text, from its characters."""
    return "".join(c["c"] for c in span["chars"])


def _font_objects(path: str) -> list[tuple[str, str, str]]:
    """Each font object the file's first page uses: its name, kind and program's format.

    Not object numbers: saving renumbers them.
    """
    return sorted((f[3], f[2], f[1]) for f in pymupdf.open(path)[0].get_fonts(full=True))


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


def test_a_font_reached_only_by_code_is_exact_and_redraws_in_itself(coded, tmp_path):
    """No letter can be looked up in it, but its ToUnicode says which code writes each."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, "BA AB")
        eng.save(out)

    assert_equal(report.state, Fidelity.EXACT, "fidelity of a span its own font draws by code")
    [drawn] = _drawn(out)
    assert_equal((_text(drawn), drawn["font"]), ("BA AB", "Coded"), "what redrew, and in what")
    assert_equal(_font_objects(out), _font_objects(coded), "fonts on the page, none added")


def test_a_redraw_by_code_really_removes_the_old_text(coded, tmp_path):
    """Rule 4: re-read the saved file, not the open one."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        eng.remove([span])
        eng.draw(span, "BAB")
        eng.save(out)

    [drawn] = _drawn(out)
    assert_equal(drawn["font"], "Coded", "the font that redrew it")
    with MuPDFEngine(out) as saved:
        assert_true(saved.absent("ABBA"), "the old text is gone from the saved file")


def test_a_redraw_by_code_lands_where_the_original_was(coded, tmp_path):
    """On a mediabox not at 0,0, and turned for the Type0."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        eng.remove([span])
        eng.draw(span, span.text)
        eng.save(out)

    [before] = _drawn(coded)
    [after] = _drawn(out)
    assert_equal(after["font"], "Coded", "the font that redrew it")
    x0, y0 = before["chars"][0]["origin"]
    x1, y1 = after["chars"][0]["origin"]
    assert_between(abs(x1 - x0) + abs(y1 - y0), -1, _ORIGIN_TOLERANCE_PT, "first glyph moved")


def test_widths_by_code_come_from_the_font_dict(coded):
    """The browser's live check reads glyphs(), the server measure(); both read /Widths, /W."""
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        assert_equal(eng.glyphs(span), _ADVANCES, "letters it draws, to their advances")
        width = sum(_ADVANCES[ch] for ch in "BA AB") * _SIZE / _EM
        assert_equal(round(eng.measure(span, "BA AB"), 4), width, "measured width")


def test_a_letter_a_coded_font_lacks_sends_the_run_to_the_substitute(coded, tmp_path):
    """C's outline was emptied and D has no code: the fit says so, and nothing mixes faces."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        missing = eng.missing(span, "ABCD")
        eng.remove([span])
        eng.draw(span, "ABC")
        eng.save(out)

    assert_equal(missing, ["C", "D"], "letters it can't draw")
    [drawn] = _drawn(out)
    substitute = pymupdf.Font(base14_for(span.font)).name
    assert_equal((_text(drawn), drawn["font"]), ("ABC", substitute), "what redrew, and in what")
