from __future__ import annotations

import io
from collections.abc import Callable
from string import Formatter

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import Glyph

from squidpdf.core import words
from squidpdf.core.fonts.catalog import FACES, face_bytes
from tests.conftest import to_unicode, type3_copies_at

_SYMBOL_OFFSET = 0xF000  # a (3,0) cmap files code c under U+F000 + c
_EM = 1000
_OFFSET_BOX = "[100 50 712 842]"

_GLYPHS = [".notdef", "A", "B", "space", "C"]

_BY_FIRST_USE = {0x20: "A", 0x21: "B", 0x22: "space", 0x23: "C"}

# Advances in the font dict, not the font program's own 600, so a test can tell them apart.
_WIDTHS = "500 550 250 600"

_GAPPED_TEXT = "AB BA AB"
_LONE_WORD = "BA"
GAPPED_SIZE = 12.0
_GAPPED_LINES = (700, 650, 600)
GAP_EM = 0.3  # not MuPDF's 0.26 guess for a missing space, nor .notdef's 0.6: a test can tell
_ADVANCE = 600

TRANSLUCENT = 0.6

_DESCRIPTOR = (
    "<</Type/FontDescriptor/FontName/{name}/Flags 4/FontBBox[0 -200 600 800]"
    "/ItalicAngle 0/Ascent 800/Descent -200/CapHeight 700/StemV 80/FontFile2 {file}>>"
)


def square(width: int = 500) -> Glyph:
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((50 + width, 700))
    pen.lineTo((50 + width, 0))
    pen.closePath()
    return pen.glyph()


def finished_font(
    builder: FontBuilder,
    *,
    family: str,
    before_saving: Callable[[FontBuilder], None] | None = None,
) -> bytes:
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    if before_saving is not None:
        before_saving(builder)
    out = io.BytesIO()
    builder.save(out)
    return out.getvalue()


def _truetype(
    cmap: dict[int, str] | None, symbol: bool = True, family: str = "Symbolic"
) -> bytes:
    builder = FontBuilder(_EM, isTTF=True)
    builder.setupGlyphOrder(_GLYPHS)
    builder.setupCharacterMap(cmap or {})
    shaped, empty = square(), TTGlyphPen(None).glyph()
    builder.setupGlyf({n: shaped if n in ("A", "B") else empty for n in _GLYPHS})
    builder.setupHorizontalMetrics({n: (_ADVANCE, 50) for n in _GLYPHS})

    def as_asked(built: FontBuilder) -> None:
        if cmap is None:
            del built.font["cmap"]
            return
        if symbol:
            table = built.font["cmap"]
            table.tables = table.tables[:1]
            table.tables[0].platformID, table.tables[0].platEncID = 3, 0

    return finished_font(builder, family=family, before_saving=as_asked)


# Written by hand, object by object, so the font is stored as the problem PDFs store theirs.


def _embed_by_hand(
    path: str,
    streams: dict[str, bytes],
    dicts: dict[str, str],
    box: str | None = None,
    rotate: int = 0,
    resources: str = "",
    resource: str = "F1",
) -> str:
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
    doc.xref_set_key(
        page.xref, "Resources", f"<</Font<</{resource} {refs['font']}>>{resources}>>"
    )
    doc.xref_set_key(page.xref, "Contents", refs["content"])
    if box is not None:
        doc.xref_set_key(page.xref, "MediaBox", box)
    if rotate:
        doc.xref_set_key(page.xref, "Rotate", str(rotate))
    doc.save(path)
    return path


@pytest.fixture(scope="module")
def symbolic(tmp_path_factory) -> str:
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


def _in_coded_symbol(
    path: str,
    content: bytes,
    *,
    box: str | None = None,
    resources: str = "",
    resource: str = "F1",
) -> str:
    return _embed_by_hand(
        path,
        {
            "file": _truetype({_SYMBOL_OFFSET + c: n for c, n in _BY_FIRST_USE.items()}),
            "to_unicode": to_unicode(
                "<00> <FF>",
                "1 beginbfrange <20> <21> <0041> endbfrange"
                " 2 beginbfchar <22> <0020> <23> <0043> endbfchar",
            ),
            "content": content,
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Coded"),
            "font": "<</Type/Font/Subtype/TrueType/BaseFont/Coded/FirstChar 32/LastChar 35"
            f"/Widths[{_WIDTHS}]/FontDescriptor {{descriptor}}/ToUnicode {{to_unicode}}>>",
        },
        box=box,
        resources=resources,
        resource=resource,
    )


@pytest.fixture(scope="module")
def coded_symbol(tmp_path_factory) -> str:
    return _in_coded_symbol(
        str(tmp_path_factory.mktemp("coded") / "symbol.pdf"),
        b"BT /F1 12 Tf 172 700 Td <20212120> Tj ET",
        box=_OFFSET_BOX,
    )


def coded_under(path: str, resource: str) -> str:
    return _in_coded_symbol(
        path,
        f"BT /{resource} 12 Tf 172 700 Td <20212120> Tj ET"
        f" BT /{resource} 12 Tf 172 600 Td <2120> Tj ET".encode(),
        resource=resource,
    )


@pytest.fixture(scope="module")
def coded_translucent(tmp_path_factory) -> str:
    return _in_coded_symbol(
        str(tmp_path_factory.mktemp("coded") / "translucent.pdf"),
        b"q /GS1 gs BT /F1 12 Tf 172 700 Td <20212120> Tj ET Q",
        resources=f"/ExtGState<</GS1<</ca {TRANSLUCENT}>>>>",
    )


@pytest.fixture(scope="module")
def translucent(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("translucent") / "translucent.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text(
        (72, 700), "Valid until March", fontname="emb", fontsize=12, fill_opacity=TRANSLUCENT
    )
    doc.subset_fonts(verbose=False)
    doc.save(path)
    return path


@pytest.fixture(scope="module")
def coded_type0(tmp_path_factory) -> str:
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("coded") / "type0.pdf"),
        {
            "file": _truetype(None),
            "to_unicode": to_unicode(
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
def type0_listed_apart(tmp_path_factory) -> str:
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("coded") / "listed-apart.pdf"),
        {
            "file": _truetype(None),
            "to_unicode": to_unicode(
                "<0000> <FFFF>",
                "1 beginbfrange <0001> <0003> [<0041> <0042> <0020>] endbfrange"
                " 1 beginbfchar <0004> <0043> endbfchar",
            ),
            "content": b"BT /F1 12 Tf 172 700 Td <0001000200020001> Tj ET",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Coded")
            .replace("/Flags 4", "/Flags 34")
            .replace("/ItalicAngle 0", "/ItalicAngle -12"),
            "cid": "<</Type/Font/Subtype/CIDFontType2/BaseFont/Coded"
            "/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
            f"/FontDescriptor {{descriptor}}/DW 1000/W[1[{_WIDTHS}]]/CIDToGIDMap/Identity>>",
            "inner_fonts": "[{cid}]",
            "font": "<</Type/Font/Subtype/Type0/BaseFont/Coded/Encoding/Identity-H"
            "/DescendantFonts {inner_fonts}/ToUnicode {to_unicode}>>",
        },
    )


@pytest.fixture(
    scope="module",
    params=["9999 0 R", "[9999 0 R]"],
    ids=["its list of inner fonts", "its inner font"],
)
def type0_pointing_nowhere(tmp_path_factory, request) -> str:
    folder = tmp_path_factory.mktemp("nowhere")
    doc = pymupdf.open(coded_under(str(folder / "coded.pdf"), "F1"))
    page = doc[0]
    _value_type, font = doc.xref_get_key(page.xref, "Resources/Font/F1")
    damaged = doc.get_new_xref()
    doc.update_object(
        damaged,
        "<</Type/Font/Subtype/Type0/BaseFont/Coded/Encoding/Identity-H"
        f"/DescendantFonts {request.param}>>",
    )
    doc.xref_set_key(page.xref, "Resources/Font", f"<</F0 {damaged} 0 R/F1 {font}>>")
    path = str(folder / "pointing-nowhere.pdf")
    doc.save(path)
    return path


@pytest.fixture(scope="module")
def corrupt(tmp_path_factory) -> str:
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


@pytest.fixture(scope="module")
def type3(tmp_path_factory) -> str:
    """A Type3's /BaseFont isn't read; with no /Name its text calls it by its object."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("type3") / "type3.pdf"),
        {
            "content": b"BT /F1 12 Tf 72 700 Td (Hi) Tj ET",
            "letter": b"600 0 0 0 600 700 d1 0 0 600 700 re f",
        },
        {
            "descriptor": "<</Type/FontDescriptor/FontName/ABCDEF+Drawn-Bold/Flags 32"
            "/FontWeight 700/ItalicAngle 0/FontBBox[0 0 600 700]>>",
            "font": "<</Type/Font/Subtype/Type3/BaseFont/Drawn-Bold/FontBBox[0 0 600 700]"
            "/FontMatrix[0.001 0 0 0.001 0 0]/FirstChar 72/LastChar 105"
            "/Encoding<</Type/Encoding/Differences[72/H 105/i]>>"
            "/Widths[600 " + "0 " * 32 + "600]/CharProcs<</H {letter}/i {letter}>>"
            "/FontDescriptor {descriptor}>>",
        },
    )


_NARROW_PERIOD, TABULAR_PERIOD = 0x2E, 0x2F
LIST_MARKER = "1.2."


@pytest.fixture(scope="module")
def tabular_period(tmp_path_factory) -> str:
    marker = f"31{TABULAR_PERIOD:02X}32{TABULAR_PERIOD:02X}"
    periods = (_NARROW_PERIOD, TABULAR_PERIOD)
    pairs = " ".join(f"<{code:02X}> <002E>" for code in periods) + " <31> <0031> <32> <0032>"
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("type3") / "tabular.pdf"),
        {
            "content": f"BT /F1 12 Tf 72 700 Td <{marker}> Tj ET".encode(),
            "letter": b"600 0 0 0 600 700 d1 0 0 600 700 re f",
            "to_unicode": to_unicode("<00> <FF>", f"4 beginbfchar {pairs} endbfchar"),
        },
        {
            "descriptor": "<</Type/FontDescriptor/FontName/ABCDEF+Drawn-Regular/Flags 32"
            "/ItalicAngle 0/FontBBox[0 0 600 700]>>",
            "font": "<</Type/Font/Subtype/Type3/FontBBox[0 0 600 700]"
            f"/FontMatrix[0.001 0 0 0.001 0 0]/FirstChar {_NARROW_PERIOD}/LastChar 50"
            f"/Encoding<</Type/Encoding/Differences[{_NARROW_PERIOD}/period"
            f" {TABULAR_PERIOD}/period.tf 49/one 50/two]>>"
            "/Widths[100 600 0 600 600]"
            "/CharProcs<</period {letter}/period.tf {letter}/one {letter}/two {letter}>>"
            "/ToUnicode {to_unicode}/FontDescriptor {descriptor}>>",
        },
    )


@pytest.fixture(scope="module")
def type3_copies(tmp_path_factory) -> str:
    return type3_copies_at(str(tmp_path_factory.mktemp("type3") / "copies.pdf"))


@pytest.fixture(scope="module")
def gapped(tmp_path_factory) -> str:
    path = str(tmp_path_factory.mktemp("gapped") / "gapped.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    font = _truetype({ord("A"): "A", ord("B"): "B"}, symbol=False, family="Gapped")
    page.insert_font(fontname="gap", fontbuffer=font)
    [(_xref, _ext, _kind, _name, resource, _encoding)] = page.get_fonts()
    words = _GAPPED_TEXT.split()
    step = (len(words[0]) * _ADVANCE / _EM + GAP_EM) * GAPPED_SIZE
    start = f"BT /{resource} {GAPPED_SIZE} Tf"
    one_by_one = [
        f"{start} {72 + i * step} {_GAPPED_LINES[0]} Td <{_ids(word)}> Tj ET"
        for i, word in enumerate(words)
    ]
    gap = -round(GAP_EM * _EM)
    one_run = f" {gap} ".join(f"<{_ids(word)}>" for word in words)
    content = [
        *one_by_one,
        f"{start} 72 {_GAPPED_LINES[1]} Td [{one_run}] TJ ET",
        f"{start} 72 {_GAPPED_LINES[2]} Td <{_ids(_LONE_WORD)}> Tj ET",
    ]
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, "\n".join(content).encode())
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    doc.save(path)
    return path


def _ids(word: str) -> str:
    return "".join(f"{_GLYPHS.index(letter):04x}" for letter in word)


@pytest.fixture(params=["coded_symbol", "coded_type0"])
def coded(request) -> str:
    return request.getfixturevalue(request.param)


MERGED_TEXTS = ("Hello there", "Yearly quiz")
_MERGED_SIZE = 14.0


def _merged(
    path: str, fonts: tuple[bytes, bytes], texts: tuple[str, str] = MERGED_TEXTS
) -> str:
    merged = pymupdf.open()
    for text, font in zip(texts, fonts, strict=True):
        single = pymupdf.open()
        page = single.new_page()
        page.insert_font(fontname="own", fontbuffer=font)
        page.insert_text((72, 96), text, fontname="own", fontsize=_MERGED_SIZE)
        single.subset_fonts(verbose=False)
        merged.insert_pdf(pymupdf.open("pdf", single.tobytes()))
    merged.save(path)
    return path


@pytest.fixture(scope="module")
def merged(tmp_path_factory) -> str:
    times = pymupdf.Font("tiro").buffer
    return _merged(str(tmp_path_factory.mktemp("merged") / "letters.pdf"), (times, times))


@pytest.fixture(scope="module")
def merged_coded(tmp_path_factory) -> str:
    """MuPDF drops a TrueType's cmap when trimming: only ToUnicode says what a code draws."""
    sans = face_bytes(FACES["Liberation Sans Regular"])
    return _merged(str(tmp_path_factory.mktemp("merged") / "codes.pdf"), (sans, sans))


@pytest.fixture(scope="module")
def merged_apart(tmp_path_factory) -> str:
    times = pymupdf.Font("tiro").buffer
    path = str(tmp_path_factory.mktemp("merged") / "apart.pdf")
    return _merged(path, (times, times), texts=(MERGED_TEXTS[0], "Yak"))


@pytest.fixture(scope="module")
def merged_unlike(tmp_path_factory) -> str:
    times, helvetica = pymupdf.Font("tiro").buffer, pymupdf.Font("helv").buffer
    path = _merged(str(tmp_path_factory.mktemp("merged") / "unlike.pdf"), (times, helvetica))
    doc = pymupdf.open(path)
    [(_xref, _ext, _kind, times_name, *_)] = doc[0].get_fonts()
    [(helvetica_xref, *_)] = doc[1].get_fonts()
    # A PDF name writes each space as #20.
    doc.xref_set_key(helvetica_xref, "BaseFont", "/" + times_name.replace(" ", "#20"))
    renamed = path.replace(".pdf", "-renamed.pdf")
    doc.save(renamed)
    return renamed


def placeholders(entry: words.CatalogEntry) -> frozenset[str]:
    forms = [entry] if isinstance(entry, str) else entry.values()
    return frozenset(
        name for form in forms for _text, name, _spec, _conv in Formatter().parse(form) if name
    )
