"""Hand-built PDFs whose fonts can only be reached by code, shared by the core tests."""

from __future__ import annotations

import io

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import Glyph

_SYMBOL_OFFSET = 0xF000  # a (3,0) cmap files code c under U+F000 + c
_EM = 1000
_OFFSET_BOX = "[100 50 712 842]"  # a mediabox not at 0,0

# Glyph ids: A 1, B 2, space 3, C 4. C's outline is emptied, as a subset leaves one.
_GLYPHS = [".notdef", "A", "B", "space", "C"]

# Codes numbered by first use, as Chrome writes them: 0x20 is A.
_BY_FIRST_USE = {0x20: "A", 0x21: "B", 0x22: "space", 0x23: "C"}

# Advances in the font dict, not the font program's own 600, so a test can tell them apart.
# test_fidelity.py's _ADVANCES are these, by letter.
_WIDTHS = "500 550 250 600"

_DESCRIPTOR = (
    "<</Type/FontDescriptor/FontName/{name}/Flags 4/FontBBox[0 -200 600 800]"
    "/ItalicAngle 0/Ascent 800/Descent -200/CapHeight 700/StemV 80/FontFile2 {file}>>"
)


def _square() -> Glyph:
    """A plain filled square, 500 wide and 700 tall.

    The tests only ask whether a letter has a shape, not what it looks like,
    so A and B are both drawn as this.
    """
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((550, 700))
    pen.lineTo((550, 0))
    pen.closePath()
    return pen.glyph()


def _truetype(cmap: dict[int, str] | None, symbol: bool = True) -> bytes:
    """A tiny made-up TrueType font, built fresh for the tests.

    A and B are squares; space, C and .notdef have no shape. C is empty on
    purpose, the way a trimmed-down font leaves out letters it didn't need.
    `cmap` is its own letter lookup: a (3,0) symbol one, or none at all.
    """
    fb = FontBuilder(_EM, isTTF=True)
    fb.setupGlyphOrder(_GLYPHS)
    fb.setupCharacterMap(cmap or {})
    square, empty = _square(), TTGlyphPen(None).glyph()
    fb.setupGlyf({n: square if n in ("A", "B") else empty for n in _GLYPHS})
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


# The fixtures below write a one-page PDF by hand, object by object, so the
# font is stored exactly the way the problem PDFs store theirs.


def _to_unicode(codespace: str, body: str) -> bytes:
    """A ToUnicode CMap: the list saying which letter each code is."""
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


@pytest.fixture(scope="module")
def corrupt(tmp_path_factory) -> str:
    """ABBA in a stored TrueType whose font program is garbage: MuPDF can't open it."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("corrupt") / "corrupt.pdf"),
        {
            "file": b"\x00\x01\x00\x00" + b"not really a font " * 40,
            "content": b"BT /F1 12 Tf 72 700 Td (ABBA) Tj ET",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Broken"),
            "font": "<</Type/Font/Subtype/TrueType/BaseFont/Broken/FirstChar 65/LastChar 66"
            "/Widths[600 600]/FontDescriptor {descriptor}>>",
        },
    )


@pytest.fixture(params=["coded_symbol", "coded_type0"])
def coded(request) -> str:
    """A font the file reaches only by code, in either shape."""
    return request.getfixturevalue(request.param)
