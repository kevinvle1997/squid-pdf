"""Hand-built PDFs with the odd fonts real files carry, shared by the core tests."""

from __future__ import annotations

import io
from pathlib import Path
from string import Formatter

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph

from squidpdf.core.fonts.catalog import FACES, face_bytes

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

# The gapped fixture: its words, their size, and the gap between them, in ems.
GAPPED_TEXT = "AB BA AB"
LONE_WORD = "BA"
GAPPED_SIZE = 12.0
GAPPED_LINES = (700, 650, 600)  # each line's baseline, in PDF space
GAP_EM = 0.3  # not MuPDF's 0.26 guess for a missing space, nor .notdef's 0.6: a test can tell
_ADVANCE = 600  # every glyph's advance in _truetype's fonts, per 1000 em

TRANSLUCENT = 0.6  # the translucent fixtures' opacity: 153 of 255, as MuPDF reads it back

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


def _truetype(
    cmap: dict[int, str] | None, symbol: bool = True, family: str = "Symbolic"
) -> bytes:
    """A tiny made-up TrueType font, built fresh for the tests.

    A and B are squares; space, C and .notdef have no shape. C is empty on
    purpose, the way a trimmed-down font leaves out letters it didn't need.
    `cmap` is its own letter lookup: a (3,0) symbol one, a Unicode one when
    `symbol` is False, or none at all.
    """
    builder = FontBuilder(_EM, isTTF=True)
    builder.setupGlyphOrder(_GLYPHS)
    builder.setupCharacterMap(cmap or {})
    square, empty = _square(), TTGlyphPen(None).glyph()
    builder.setupGlyf({n: square if n in ("A", "B") else empty for n in _GLYPHS})
    builder.setupHorizontalMetrics({n: (_ADVANCE, 50) for n in _GLYPHS})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    if cmap is None:
        del builder.font["cmap"]
    elif symbol:
        table = builder.font["cmap"]
        table.tables = table.tables[:1]
        table.tables[0].platformID, table.tables[0].platEncID = 3, 0
    out = io.BytesIO()
    builder.save(out)
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
    resources: str = "",
) -> str:
    """One page drawing streams["content"] with /F1 as dicts["font"].

    Each dict names another object as {its key}; the font program is {file}.
    `resources` goes into the page's resources beside the font.
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
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {refs['font']}>>{resources}>>")
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
def coded_translucent(tmp_path_factory) -> str:
    """The symbol fixture's ABBA, painted at TRANSLUCENT opacity by a graphics state (/GS1)."""
    return _embed_by_hand(
        str(tmp_path_factory.mktemp("coded") / "translucent.pdf"),
        {
            "file": _truetype({_SYMBOL_OFFSET + c: n for c, n in _BY_FIRST_USE.items()}),
            "to_unicode": _to_unicode(
                "<00> <FF>",
                "1 beginbfrange <20> <21> <0041> endbfrange"
                " 2 beginbfchar <22> <0020> <23> <0043> endbfchar",
            ),
            "content": b"q /GS1 gs BT /F1 12 Tf 172 700 Td <20212120> Tj ET Q",
        },
        {
            "descriptor": _DESCRIPTOR.replace("{name}", "Coded"),
            "font": "<</Type/Font/Subtype/TrueType/BaseFont/Coded/FirstChar 32/LastChar 35"
            f"/Widths[{_WIDTHS}]/FontDescriptor {{descriptor}}/ToUnicode {{to_unicode}}>>",
        },
        resources=f"/ExtGState<</GS1<</ca {TRANSLUCENT}>>>>",
    )


@pytest.fixture(scope="module")
def translucent(tmp_path_factory) -> str:
    """One line in a stored Times Roman, trimmed and painted at TRANSLUCENT opacity.

    Trimmed, so a letter it didn't use sends a redraw to the substitute.
    """
    path = str(tmp_path_factory.mktemp("translucent") / "translucent.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    # "emb" is only the name the page files the font under; "tiro" is MuPDF's Times Roman.
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text(
        (72, 700), "Valid until March", fontname="emb", fontsize=12, fill_opacity=TRANSLUCENT
    )
    doc.subset_fonts(verbose=False)
    doc.save(path)
    return path


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


@pytest.fixture(scope="module")
def gapped(tmp_path_factory) -> str:
    """Words in a stored font with no space, GAP_EM apart, as pdfTeX writes them.

    The font maps A and B but no space. Three lines, each a way MuPDF reads gaps:
    - GAPPED_TEXT, word by word: each gap is a piece of its own.
    - GAPPED_TEXT in one run (TJ): the gaps are spaces inside one piece.
    - LONE_WORD: no gap but the page's.
    """
    path = str(tmp_path_factory.mktemp("gapped") / "gapped.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    font = _truetype({ord("A"): "A", ord("B"): "B"}, symbol=False, family="Gapped")
    # "gap" is only the name the page files the font under; MuPDF writes it by glyph id.
    page.insert_font(fontname="gap", fontbuffer=font)
    [(_xref, _ext, _kind, _name, resource, _encoding)] = page.get_fonts()
    words = GAPPED_TEXT.split()
    step = (len(words[0]) * _ADVANCE / _EM + GAP_EM) * GAPPED_SIZE
    start = f"BT /{resource} {GAPPED_SIZE} Tf"
    # PDF space: y counts up from the bottom of the page.
    one_by_one = [
        f"{start} {72 + i * step} {GAPPED_LINES[0]} Td <{_ids(word)}> Tj ET"
        for i, word in enumerate(words)
    ]
    gap = -round(GAP_EM * _EM)  # a TJ number moves the pen back, in thousandths of an em
    one_run = f" {gap} ".join(f"<{_ids(word)}>" for word in words)
    content = [
        *one_by_one,
        f"{start} 72 {GAPPED_LINES[1]} Td [{one_run}] TJ ET",
        f"{start} 72 {GAPPED_LINES[2]} Td <{_ids(LONE_WORD)}> Tj ET",
    ]
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, "\n".join(content).encode())
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    doc.save(path)
    return path


def _ids(word: str) -> str:
    """A word as the glyph ids the gapped fixture writes: A is 1, B is 2 (see _GLYPHS)."""
    return "".join(f"{_GLYPHS.index(letter):04x}" for letter in word)


@pytest.fixture(params=["coded_symbol", "coded_type0"])
def coded(request) -> str:
    """A font the file reaches only by code, in either shape."""
    return request.getfixturevalue(request.param)


# The merged fixtures' two pages: each copy draws only its own page's letters.
# Pooled, "Yearly Hello" draws; "Yes" never can, since neither page drew an s.
MERGED_TEXTS = ("Hello there", "Yearly quiz")
_MERGED_SIZE = 14.0


def _merged(
    path: str, fonts: tuple[bytes, bytes], texts: tuple[str, str] = MERGED_TEXTS
) -> str:
    """Two one-page PDFs, each with its own trimmed copy of a font, joined into one.

    As merging two documents leaves them: every page keeps the copy it came
    with, trimmed (subset) to that page's letters, under the same name with a
    different subset prefix. Page 0 draws texts[0] in fonts[0], page 1
    texts[1] in fonts[1].
    """
    merged = pymupdf.open()
    for text, font in zip(texts, fonts, strict=True):
        single = pymupdf.open()
        page = single.new_page()
        # "own" is only the name the page files the font under.
        page.insert_font(fontname="own", fontbuffer=font)
        page.insert_text((72, 96), text, fontname="own", fontsize=_MERGED_SIZE)
        single.subset_fonts(verbose=False)
        # Saved and reopened, as two real files would be, before joining.
        merged.insert_pdf(pymupdf.open("pdf", single.tobytes()))
    merged.save(path)
    return path


@pytest.fixture(scope="module")
def merged(tmp_path_factory) -> str:
    """Two pages, each with its own trimmed copy of Times, reached by letter.

    MuPDF's Times ("tiro") is a CFF font, which keeps its letter table when
    trimmed, so the engine writes it by letter. Not a face we ship, so the
    substitute (Liberation Serif) can't be mistaken for it.
    """
    times = pymupdf.Font("tiro").buffer
    return _merged(str(tmp_path_factory.mktemp("merged") / "letters.pdf"), (times, times))


@pytest.fixture(scope="module")
def merged_coded(tmp_path_factory) -> str:
    """Two pages, each with its own trimmed copy of Liberation Sans, reached only by code.

    MuPDF drops a TrueType's letter table (cmap) when it trims one, so only
    the letter list (ToUnicode) says which code draws each letter. Both copies
    keep every glyph at its old number and empty the ones the page didn't use:
    the same code draws Y in page 1's copy and nothing in page 0's.
    """
    sans = face_bytes(FACES["Liberation Sans Regular"])
    return _merged(str(tmp_path_factory.mktemp("merged") / "codes.pdf"), (sans, sans))


@pytest.fixture(scope="module")
def merged_apart(tmp_path_factory) -> str:
    """The merged file, but page 1 draws only "Yak": no letter in common with page 0.

    Nothing to check its widths against, so nothing says it's the same font.
    """
    times = pymupdf.Font("tiro").buffer
    path = str(tmp_path_factory.mktemp("merged") / "apart.pdf")
    return _merged(path, (times, times), texts=(MERGED_TEXTS[0], "Yak"))


@pytest.fixture(scope="module")
def merged_unlike(tmp_path_factory) -> str:
    """The merged file, but page 1's copy is Helvetica given page 0's name.

    Two different fonts under one name, as two releases of a font or a
    renamed one leave them: page 1's e, l and r are wider than Times'.
    """
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


# Poppins as Google's collection has it, with its licence beside it: the whole font.
POPPINS = Path(__file__).parent / "fonts" / "Poppins-Regular.ttf"
# What the Poppins fixture's page draws; "Yearly Hello" needs Y, a and y besides.
POPPINS_TEXT = "Hello there"


@pytest.fixture(scope="module")
def poppins_subset(tmp_path_factory) -> str:
    """One line in a trimmed copy of Poppins, named as a real trimmed copy is.

    Trimmed with fontTools, which keeps its letter table, so the engine writes
    it by letter. MuPDF files an added font as "Poppins Regular"; a real file
    names it `ABCDEF+Poppins-Regular`, so that's the name it's given, on the
    font and the one inside it.
    """
    trimmer = Subsetter(Options())
    trimmer.populate(text=POPPINS_TEXT)
    font = TTFont(POPPINS)
    trimmer.subset(font)
    trimmed_file = io.BytesIO()
    font.save(trimmed_file)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="own", fontbuffer=trimmed_file.getvalue())
    page.insert_text((72, 96), POPPINS_TEXT, fontname="own", fontsize=_MERGED_SIZE)
    [(xref, *_)] = page.get_fonts()
    _kind, descendants = doc.xref_get_key(xref, "DescendantFonts")
    for font_xref in (xref, int(descendants.strip("[]").split()[0])):
        doc.xref_set_key(font_xref, "BaseFont", "/ABCDEF+Poppins-Regular")
    path = str(tmp_path_factory.mktemp("google") / "poppins.pdf")
    doc.save(path)
    return path


def placeholders(sentence: str) -> frozenset[str]:
    """The `{name}`s a sentence takes."""
    return frozenset(name for _text, name, _spec, _conv in Formatter().parse(sentence) if name)
