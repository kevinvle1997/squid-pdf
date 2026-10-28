"""Shared fixtures for every test under tests/, however deep."""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pytest
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import DriverFactory, Engine, QuarterTurn, open_pdf, words
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.documents.attached import AttachedFiles
from squidpdf.documents.store import FOLDER_STORE

# pytest only explains the asserts in test modules; this has it explain the helpers' too,
# so a failing assert_equal shows a diff, not two whole values. Before any test imports them.
pytest.register_assert_rewrite("tests.helpers")

_POSTSCRIPT_NAME = 6  # the font's name table entry a PDF names it by
_WIDTH_LIST_CODES = range(32, 127)  # the codes a width list covers: the keyboard's letters
_WIDTH_LIST_EM = 1000  # a width list's unit: a thousandth of the size
_POPPINS_SIZE = 14.0

# The sample's two pages, counted from 0 as spans count them.
REFERENCED_PAGE = 0  # fonts named but not in the file: edits use a substitute
EMBEDDED_PAGE = 1  # one font in the file, trimmed to the letters the page uses
TAGGED_LINES = ["First page", "Second page"]  # the tagged fixture's line on each page


@dataclass(frozen=True, slots=True)
class StoreWith:
    """The folder store, with a test's own open, touch or sweep: a race it sets up."""

    opens_original: Callable[[Path], Engine] = FOLDER_STORE.open_original
    touches: Callable[[Path], float] = FOLDER_STORE.touch
    sweeps: Callable[[], None] = FOLDER_STORE.sweep

    @property
    def driver_factory(self) -> DriverFactory:
        """The folder store's."""
        return FOLDER_STORE.driver_factory

    def create(self, owner_digest: str) -> tuple[str, Path]:
        """The folder store's."""
        return FOLDER_STORE.create(owner_digest)

    def find(self, doc_id: str) -> tuple[Path, str] | None:
        """The folder store's."""
        return FOLDER_STORE.find(doc_id)

    def open_to_analyse(self, folder: Path, attached: AttachedFiles) -> Engine:
        """The folder store's."""
        return FOLDER_STORE.open_to_analyse(folder, attached)

    def delete(self, folder: Path) -> None:
        """The folder store's."""
        FOLDER_STORE.delete(folder)

    def open_original(self, folder: Path) -> Engine:
        """The test's, or the folder store's."""
        return self.opens_original(folder)

    def touch(self, folder: Path) -> float:
        """The test's, or the folder store's."""
        return self.touches(folder)

    def sweep(self) -> None:
        """The test's, or the folder store's."""
        self.sweeps()


# A log line's fixed shape: time (UTC), level, outcome, module, event, then key=value fields.
LOG_LINE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (INFO |WARN |ERROR) (noted  |skipped|failed ) "
    r"[a-z_.]+ [a-z_]+( [a-z_]+=\S+)*"
)
# Where each line starts, when a traceback or a debug follows it on the lines after.
_LOG_LINE_START = re.compile(r"^\d{4}-\d\d-\d\dT", re.MULTILINE)


def log_lines(output: str) -> list[str]:
    """The first line of each log entry in captured output; what follows one isn't counted."""
    return [line for line in output.splitlines() if _LOG_LINE_START.match(line)]


@pytest.fixture(scope="session", autouse=True)
def no_fetch() -> Iterator[None]:
    """Nothing in the suite reaches Google: set before any worker starts, so they inherit it."""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_NO_FETCH", "1")
        yield


def each_span(blocks: list[dict]) -> Iterator[dict]:
    """Every span of text in get_text's blocks, in reading order."""
    for block in blocks:
        # .get: an image block has no lines.
        for line in block.get("lines", []):
            yield from line["spans"]


def cannot_cut(_subsetter: Subsetter, _font: TTFont) -> None:
    """Fails, as fontTools can on an odd font: patched over `Subsetter.subset`."""
    raise ValueError("fontTools can't cut this font")


def saved_as(face: str) -> str:
    """The name a saved PDF gives a face we ship, e.g. "Carlito-Bold" for "Carlito Bold".

    Read from the shipped file itself: its PostScript name, which MuPDF writes.
    """
    font = TTFont(io.BytesIO(face_bytes(FACES[face])))
    return str(font["name"].getDebugName(_POSTSCRIPT_NAME))


def stored_file(path: str, page: int, font: str) -> bytes:
    """The font file a saved page stores under the name `font`."""
    doc = pymupdf.open(path)
    [font_object] = [
        font_object
        for font_object, _ext, _kind, name, *_ in doc[page].get_fonts()
        if name == font
    ]
    _name, _ext, _kind, buffer = doc.extract_font(font_object)
    return buffer


def named_only(
    path: str, base_font: str, flags: int | None = None, *, italic_angle: float = 0
) -> str:
    """One line in a font the file only names, never stores, as Word does with Calibri.

    Written in MuPDF's Helvetica, then renamed: nothing here reads the letters'
    shapes, only the font's name and, when `flags` is given, its description,
    which slants by `italic_angle`.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Hello there", fontname="helv", fontsize=12)
    [(font_object, *_rest)] = page.get_fonts()
    doc.xref_set_key(font_object, "BaseFont", f"/{base_font}")
    if flags is not None:
        descriptor = (
            f"<</Type/FontDescriptor/FontName/{base_font}/Flags {flags}"
            f"/ItalicAngle {italic_angle}>>"
        )
        doc.xref_set_key(font_object, "FontDescriptor", descriptor)
    doc.save(path)
    return path


def named_with_widths(path: str, face: str, *, base_font: str) -> str:
    """One line in a font the file only names, with the width list Word writes for it.

    The widths are `face`'s, a face we ship, for codes 32 to 126 in the usual
    encoding: Liberation Sans's are Arial's, so "Arial" names a font its file matches.
    """
    program = pymupdf.Font(fontbuffer=face_bytes(FACES[face]))
    widths = [
        round(program.text_length(chr(code), fontsize=1) * _WIDTH_LIST_EM)
        for code in _WIDTH_LIST_CODES
    ]
    path = named_only(path, base_font)
    doc = pymupdf.open(path)
    [(font_object, *_rest)] = doc[0].get_fonts()
    doc.xref_set_key(font_object, "Subtype", "/TrueType")
    doc.xref_set_key(font_object, "FirstChar", str(_WIDTH_LIST_CODES.start))
    doc.xref_set_key(font_object, "LastChar", str(_WIDTH_LIST_CODES.stop - 1))
    doc.xref_set_key(font_object, "Widths", "[" + " ".join(map(str, widths)) + "]")
    doc.saveIncr()
    return path


def name_two_byte_font(doc: pymupdf.Document, font_object: int, name: str) -> int:
    """Names a two-byte font and the one inside it `ABCDEF+<name>`, as a real trimmed copy is.

    Returns the one inside, which keeps the font's description.
    """
    _kind, descendants = doc.xref_get_key(font_object, "DescendantFonts")
    inner = int(descendants.strip("[]").split()[0])
    for each_font in (font_object, inner):
        doc.xref_set_key(each_font, "BaseFont", f"/ABCDEF+{name}")
    return inner


def to_unicode(codespace: str, body: str) -> bytes:
    """A ToUnicode CMap: the list saying which letter each code is."""
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap"
        " /CMapName /Test def /CMapType 2 def"
        f" 1 begincodespacerange {codespace} endcodespacerange {body}"
        " endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()


# Two Type3 copies of one font: each letter's code, the font's glyph number as Chrome
# writes it, and the line each copy draws. Both draw e, l and o; only the second Y and w.
_TYPE3_CODES = {"H": 1, "e": 2, "l": 3, "o": 4, "Y": 5, "w": 6}
TYPE3_TEXTS = ("Hello", "Yellow")


def type3_copies_at(path: str) -> str:
    """Hello, then Yellow, each line in its own Type3 copy of one font, as Chrome prints one.

    Each letter is the same square drawing: only whether it draws is read. Each
    copy has only its own line's letters, by the same codes, in its letter list
    (ToUnicode). With no /Name, only their descriptions name the font they share.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    letter = doc.get_new_xref()
    doc.update_object(letter, "<<>>")
    doc.update_stream(letter, b"600 0 0 0 600 700 d1 0 0 600 700 re f")
    fonts, lines = [], []
    for number, text in enumerate(TYPE3_TEXTS):
        codes = {_TYPE3_CODES[ch]: ch for ch in text}
        fonts.append(
            f"/F{number} {_type3_copy(doc, codes, letter=letter, prefix='AB'[number])}"
        )
        shown = "".join(f"{_TYPE3_CODES[ch]:02X}" for ch in text)
        lines.append(f"BT /F{number} 12 Tf 72 {700 - 50 * number} Td <{shown}> Tj ET")
    content = doc.get_new_xref()
    doc.update_object(content, "<<>>")
    doc.update_stream(content, "\n".join(lines).encode())
    doc.xref_set_key(page.xref, "Resources", f"<</Font<<{' '.join(fonts)}>>>>")
    doc.xref_set_key(page.xref, "Contents", f"{content} 0 R")
    doc.save(path)
    return path


def _type3_copy(
    doc: pymupdf.Document, codes: dict[int, str], *, letter: int, prefix: str
) -> str:
    """A Type3 copy drawing `codes`, each as `letter`, its description named with `prefix`."""
    last = max(_TYPE3_CODES.values())
    widths = " ".join("600" if code in codes else "0" for code in range(1, last + 1))
    names = " ".join(f"{code}/g{code}" for code in codes)
    drawings = "".join(f"/g{code} {letter} 0 R" for code in codes)
    pairs = " ".join(f"<{code:02X}> <{ord(ch):04X}>" for code, ch in codes.items())
    letter_list = to_unicode("<01> <FF>", f"{len(codes)} beginbfchar {pairs} endbfchar")
    xref = {name: doc.get_new_xref() for name in ("font", "descriptor", "to_unicode")}
    for x in xref.values():
        doc.update_object(x, "<<>>")
    doc.update_stream(xref["to_unicode"], letter_list)
    doc.update_object(
        xref["descriptor"],
        f"<</Type/FontDescriptor/FontName/{prefix * 3}AAA+Drawn-Regular/Flags 32"
        "/FontBBox[0 0 600 700]/ItalicAngle 0>>",
    )
    doc.update_object(
        xref["font"],
        "<</Type/Font/Subtype/Type3/FontBBox[0 0 600 700]/FontMatrix[0.001 0 0 0.001 0 0]"
        f"/FirstChar 1/LastChar {last}/Widths[{widths}]"
        f"/Encoding<</Type/Encoding/Differences[{names}]>>/CharProcs<<{drawings}>>"
        f"/ToUnicode {xref['to_unicode']} 0 R/FontDescriptor {xref['descriptor']} 0 R>>",
    )
    return f"{xref['font']} 0 R"


def drawn_with(
    path: str,
    *,
    setting: str = "",
    rotate: int = 0,
    text: str = "Terms of payment",
    face: str | None = None,
) -> str:
    """One line of `text` in stored, trimmed Times or `face`, drawn with `setting` and `rotate`.

    `setting` moves where each letter lands, not which it is, so the font still draws them all.
    """
    font_file = pymupdf.Font("tiro").buffer if face is None else face_bytes(FACES[face])
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=font_file)
    page.insert_text((72, 300), text, fontname="emb", fontsize=12, rotate=rotate)
    [xref] = page.get_contents()
    drawing = doc.xref_stream(xref).replace(b"BT", b"BT " + setting.encode(), 1)
    doc.update_stream(xref, drawing)
    doc.subset_fonts(verbose=False)
    doc.save(path)
    return path


_SERIF = "Liberation Serif Regular"  # the face `in_a_copy_lacking` stores a copy of


def in_a_copy_lacking(
    path: str, *, text: str, lacking: str = "", stored: bool = True, turn_ccw: QuarterTurn = 0
) -> str:
    """One line of `text` in a whole, stored Liberation Serif with no shape for `lacking`.

    Each of `lacking` the face has is emptied, as a trimmed copy leaves a letter it didn't
    need, though the page still draws it; whole, so any other letter can be typed. Not
    `stored`, it's in MuPDF's Times, which the file only names, and `lacking` is moot.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    font_name = "tiro"
    if stored:
        font_name = "emb"
        page.insert_font(fontname=font_name, fontbuffer=_emptied(_SERIF, lacking))
    page.insert_text((72, 300), text, fontname=font_name, fontsize=12, rotate=turn_ccw)
    doc.save(path)
    return path


def _emptied(face: str, letters: str) -> bytes:
    """`face`'s file with no shape for each of `letters` it has."""
    font = TTFont(io.BytesIO(face_bytes(FACES[face])))
    glyph_names = font["cmap"].getBestCmap()
    for letter in letters:
        # .get: a letter the face never had is lacking already.
        name = glyph_names.get(ord(letter))
        if name is not None:
            font["glyf"][name] = TTGlyphPen(None).glyph()
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """A two-page contract with one kind of font on each page.

    Page 1 only names its fonts ("tibo", "tiro": MuPDF's Times, never put in the file). Page 2
    puts Times Roman in the file for two lines, then trims it to their letters, so é has no
    shape.
    """
    path = tmp_path_factory.mktemp("fx") / "sample.pdf"
    doc = pymupdf.open()

    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        "Made on 14 March 2026 between Wescott and Rowe.",
        fontname="tiro",
        fontsize=11,
    )

    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text(
        (72, 96),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    embedded.insert_text(
        (72, 124), "Invoices are due within thirty days.", fontname="emb", fontsize=11
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()
    return str(path)


# The form fixture's facts: its page's own line, the value its text field shows, and the
# hint the page prints inside an empty field.
FORM_LINE = "Name: Ada Byron"
FORM_FIELD_VALUE = "SSN 078-05-1120"
FORM_HINT = "MM/DD/YYYY"
_TEXT_FIELD = 7  # PyMuPDF's PDF_WIDGET_TYPE_TEXT, set at import, so type checkers can't see it


@pytest.fixture(scope="module")
def form() -> bytes:
    """One page: a line of the page's own text, a filled-in text field, and an empty one.

    The filled field's value is drawn by the widget, not the page, so it's read as a span but
    erasing the page's text can't reach it, as a filled-in form comes. The empty field holds a
    hint the page prints, as forms do, which an edit reaches.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), FORM_LINE, fontname="helv", fontsize=12)
    page.insert_text((80, 172), FORM_HINT, fontname="helv", fontsize=10)
    for name, value, top in (("ssn", FORM_FIELD_VALUE, 120), ("born", "", 160)):
        field = pymupdf.Widget()
        field.field_type = _TEXT_FIELD
        field.field_name = name
        field.field_value = value
        field.rect = pymupdf.Rect(72, top, 300, top + 20)
        field.text_fontsize = 12
        page.add_widget(field)
    return doc.tobytes()


@pytest.fixture
def tagged(tmp_path) -> str:
    """Two pages tagged for screen readers, as Word and browsers save them.

    Each page's line is one paragraph, and the tags point at it and back, as
    a real file's do. That is enough for MuPDF to keep a page it was told to drop.
    """
    doc = pymupdf.open()
    for number, line in enumerate(TAGGED_LINES):
        page = doc.new_page()
        page.insert_text((72, 96), line, fontname="helv", fontsize=12)
        [contents] = page.get_contents()
        marked = b"/P <</MCID 0>> BDC\n" + doc.xref_stream(contents) + b"\nEMC"
        doc.update_stream(contents, marked)
        doc.xref_set_key(page.xref, "StructParents", str(number))
    tree, document = _new_object(doc), _new_object(doc)
    paragraphs = [
        _new_object(doc, f"<</Type/StructElem/S/P/P {document} 0 R/Pg {page.xref} 0 R/K 0>>")
        for page in doc.pages()
    ]
    kids = " ".join(f"{paragraph} 0 R" for paragraph in paragraphs)
    doc.update_object(document, f"<</Type/StructElem/S/Document/P {tree} 0 R/K [{kids}]>>")
    by_page = " ".join(f"{n} [{paragraph} 0 R]" for n, paragraph in enumerate(paragraphs))
    parents = _new_object(doc, f"<</Nums [{by_page}]>>")
    root = f"<</Type/StructTreeRoot/K {document} 0 R/ParentTree {parents} 0 R>>"
    doc.update_object(tree, root)
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{tree} 0 R")
    path = tmp_path / "tagged.pdf"
    doc.save(path)
    return str(path)


def _new_object(doc: pymupdf.Document, source: str = "<<>>") -> int:
    """A new object in `doc` holding `source`; its number."""
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


@pytest.fixture
def engine(pdf):
    """The sample, open in the engine for one test."""
    with open_pdf(pdf) as engine:
        yield engine


PSEUDO = "qps"  # a tag for local use: no real language will ever have it
_PLACEHOLDER = re.compile(r"(\{\w+\})")


def pseudo_sentence(english: str) -> str:
    """`english` in the test-only language: shouted, placeholders left as they are."""
    parts = _PLACEHOLDER.split(english)
    return "".join(part if _PLACEHOLDER.fullmatch(part) else part.upper() for part in parts)


def _pseudo_entry(english: words.CatalogEntry) -> words.CatalogEntry:
    """A sentence in the test-only language, each of its forms if it takes a count."""
    if isinstance(english, str):
        return pseudo_sentence(english)
    return {form: pseudo_sentence(each) for form, each in english.items()}


@pytest.fixture
def pseudo(monkeypatch) -> str:
    """A second language to answer in, made from English, for this test only; its tag."""
    catalog = {
        key: _pseudo_entry(entry) for key, entry in words.CATALOGS[words.ENGLISH].items()
    }
    monkeypatch.setitem(words.CATALOGS, PSEUDO, catalog)
    return PSEUDO


# Poppins as Google's collection has it, with its licence beside it: the whole font.
POPPINS = Path(__file__).parent / "core" / "fonts" / "Poppins-Regular.ttf"
# What the Poppins fixture's page draws; "Yearly Hello" needs Y, a and y besides.
POPPINS_TEXT = "Hello there"


@pytest.fixture(scope="module")
def poppins_subset(tmp_path_factory) -> str:
    """One line in a trimmed copy of Poppins, named as a real trimmed copy is.

    Trimmed with fontTools, which keeps its letter table, so the engine writes it by letter.
    Named `ABCDEF+Poppins-Regular`, on the font and inside it, as a real file names it, not as
    MuPDF files an added one ("Poppins Regular").
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
    page.insert_text((72, 96), POPPINS_TEXT, fontname="own", fontsize=_POPPINS_SIZE)
    [(font_object, *_)] = page.get_fonts()
    name_two_byte_font(doc, font_object, "Poppins-Regular")
    path = str(tmp_path_factory.mktemp("google") / "poppins.pdf")
    doc.save(path)
    return path


# The facts of `with_hidden_copies`: the name its page shows, which the tests redact.
REDACTED_NAME = "Ada Quill"
OTHER_LINE = "Other line"  # the page's other line, which nothing redacts
CHOSEN = "Bob Smith"  # the choice fields' other option, the one they show
# Every copy of the name `with_hidden_copies` can make at once.
NAME_COPIES = (
    "title",
    "author",
    "xmp",
    "page_xmp",
    "annotation_xmp",
    "bookmark",
    "note",
    "free_text",
    "stale_free_text",
    "field",
    "undrawable_field",
    "choice",
    "multi_choice",
    "unlisted_field",
    "tooltip",
    "widget_contents",
    "seed_value",
    "button",
    "stamp",
    "tag",
)
# Copies in metadata that isn't read as XML, each made alone: a file has one metadata stream.
NOT_XML_COPIES = ("not_xml", "not_xml_utf16", "xmp_utf32", "xmp_doctype", "xmp_deep")
# The ways `with_hidden_copies` signs its page: plain, certifying, and checkable for years.
SIGNED = ("signature", "certified", "dss")
# PyMuPDF's PDF_WIDGET_TYPE_* values, set at import where type checkers can't see them.
_LIST_FIELD = 4
_CHOICE_FIELD = 3
_SIGNATURE_FIELD = 6
_BUTTON_FIELD = 1
_MULTI_SELECT = 1 << 21  # a list box's flag (Ff) that lets it show more than one option
# Metadata (XMP) as Acrobat writes it, plus a note to a program; both hold the name.
_XMP = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    "<?note Read by {name}?>"
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:creator><rdf:Seq><rdf:li>{name}</rdf:li></rdf:Seq></dc:creator>"
    '<dc:description><rdf:Alt><rdf:li xml:lang="x-default">Notes on {name}</rdf:li>'
    "</rdf:Alt></dc:description>"
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
    '<?xpacket end="w"?>'
)
# Metadata that isn't XML, its tags left unclosed, as a damaged file's.
_BROKEN_XMP = "<x:xmpmeta><dc:creator>{name}</x:xmpmeta>"
# Metadata whose DOCTYPE declares the name as a word of its own (an entity), used in its text.
_DECLARING_XMP = (
    '<!DOCTYPE x [<!ENTITY who "{name}">]>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:creator>&who;</dc:creator></x:xmpmeta>"
)
# The room a signature leaves for its certificates and value, zeros: nothing reads them.
_SIGNED_BYTES = bytes(64)
# How deep `xmp_deep` nests: far past how deep Python lets calls go.
_DEEP = 3000
# Each kind of metadata that isn't read as XML, as its bytes are written.
_NOT_XML = {
    "not_xml": _BROKEN_XMP.format(name=REDACTED_NAME).encode(),
    # UTF-16, with its byte order mark, as XMP may be written.
    "not_xml_utf16": ("\ufeff" + _BROKEN_XMP.format(name=REDACTED_NAME)).encode("utf-16-le"),
    # UTF-32, which XMP allows and the XML reader can't read.
    "xmp_utf32": _XMP.format(name=REDACTED_NAME).encode("utf-32"),
    "xmp_doctype": _DECLARING_XMP.format(name=REDACTED_NAME).encode(),
    "xmp_deep": (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        + "<a>" * _DEEP
        + REDACTED_NAME
        + "</a>" * _DEEP
        + "</x:xmpmeta>"
    ).encode(),
}


def with_hidden_copies(copies: Collection[str]) -> bytes:
    """A page that shows a name, and keeps a hidden copy of it as each of `copies` too.

    The comments and fields sit apart from the lines, so erasing the name's letters
    can't reach them.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), REDACTED_NAME, fontname="helv", fontsize=12)
    page.insert_text((72, 130), OTHER_LINE, fontname="helv", fontsize=12)
    info = {}  # what the file says about itself
    if "title" in copies:
        info["title"] = f"Notes on {REDACTED_NAME}"
    if "author" in copies:
        info["author"] = REDACTED_NAME
    doc.set_metadata(info)
    if "xmp" in copies:
        doc.set_xml_metadata(_XMP.format(name=REDACTED_NAME))
    if "page_xmp" in copies:
        # The page's own metadata, apart from the catalog's: any object may name one.
        doc.xref_set_key(page.xref, "Metadata", f"{_new_xmp(doc)} 0 R")
    for kind, written in _NOT_XML.items():
        if kind in copies:
            _set_metadata_bytes(doc, written)
    if "bookmark" in copies:
        doc.set_toc([[1, f"Visit by {REDACTED_NAME}", 1], [1, REDACTED_NAME, 1]])
    if "note" in copies:
        note = page.add_text_annot((500, 700), f"Call {REDACTED_NAME}")
        note.set_info(title=REDACTED_NAME, subject="Visit")
        note.update()
        doc.xref_set_key(note.xref, "RC", f"(<body><p>Call {REDACTED_NAME}</p></body>)")
    if "free_text" in copies:
        page.add_freetext_annot(
            pymupdf.Rect(72, 300, 300, 320), f"Ask {REDACTED_NAME}", fontsize=12
        )
    if "stale_free_text" in copies:
        stale = page.add_freetext_annot(
            pymupdf.Rect(320, 300, 560, 320), f"Ring {REDACTED_NAME}", fontsize=12
        )
        # Its words lose the name, but its drawing, not drawn again, keeps it.
        doc.xref_set_key(stale.xref, "Contents", "(Ring)")
    if "field" in copies:
        field = _add_field(page, "who", REDACTED_NAME, top=400)
        doc.xref_set_key(field, "DV", f"({REDACTED_NAME})")
        doc.xref_set_key(field, "RV", f"(<body><p>{REDACTED_NAME}</p></body>)")
    if "undrawable_field" in copies:
        undrawable = _add_field(page, "sealed", REDACTED_NAME, top=400, left=320)
        # No kind (FT), so MuPDF can't draw it again; a viewer shows its old drawing.
        doc.xref_set_key(undrawable, "FT", "null")
    if "choice" in copies:
        choice = _add_field(page, "pick", CHOSEN, top=450, kind=_CHOICE_FIELD)
        doc.xref_set_key(choice, "Opt", f"[({CHOSEN}) [(2) ({REDACTED_NAME})]]")
    if "multi_choice" in copies:
        both = f"[({CHOSEN}) ({REDACTED_NAME})]"
        listed = _add_field(page, "team", CHOSEN, top=450, left=320, kind=_LIST_FIELD)
        doc.xref_set_key(listed, "Ff", str(_MULTI_SELECT))
        doc.xref_set_key(listed, "Opt", both)
        doc.xref_set_key(listed, "V", both)
        doc.xref_set_key(listed, "I", "[0 1]")
    if "unlisted_field" in copies:
        unlisted = _add_field(page, "signed", REDACTED_NAME, top=500)
        # Off the form's list, as a half-flattened form leaves one.
        _kind, listed_fields = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
        left = listed_fields.replace(f"{unlisted} 0 R", "")
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", left)
    if "tooltip" in copies:
        described = _add_field(page, "described", "", top=500, left=320)
        doc.xref_set_key(described, "TU", f"(Signature of {REDACTED_NAME})")
    if "widget_contents" in copies:
        phone = _add_field(page, "phone", "", top=700)
        doc.xref_set_key(phone, "Contents", f"(Phone of {REDACTED_NAME})")
    if "seed_value" in copies:
        to_sign = _add_signature_field(page, name="to_sign", at=(320, 660))
        signer = f"<</Cert<</SubjectDN[<</CN({REDACTED_NAME})>>]>>>>"
        doc.xref_set_key(to_sign, "SV", signer)
    if "button" in copies:
        _add_button(page, f"Email {REDACTED_NAME}")
    if "stamp" in copies:
        _stamp(doc, page, f"Seen by {REDACTED_NAME}")
    if "stamp_in_image" in copies:
        _stamp(doc, page, f"Seen by {REDACTED_NAME}", in_image=True)
    if "signature" in copies:
        _sign(doc, page, name="signed_by", at=(72, 550))
    if "certified" in copies:
        certifying = _sign(doc, page, name="certified_by", at=(320, 550))
        doc.xref_set_key(doc.pdf_catalog(), "Perms", f"<</DocMDP {certifying} 0 R>>")
    if "dss" in copies:
        _sign(doc, page, name="kept_checkable", at=(72, 660))
        certificate = _new_stream(doc, _SIGNED_BYTES)
        by_signature = f"/VRI<</{'A' * 40} <</Cert[{certificate} 0 R]>>>>"  # by its hash
        doc.xref_set_key(
            doc.pdf_catalog(), "DSS", f"<</Certs[{certificate} 0 R]{by_signature}>>"
        )
    if "tag" in copies:
        _tag_a_picture(doc, f"Photo of {REDACTED_NAME}")
    if "annotation_xmp" in copies:
        # A comment naming metadata of its own, written in the page's list of comments
        # itself, not as an object of its own. Last: it writes that list again, whole.
        value_type, listed = doc.xref_get_key(page.xref, "Annots")
        comments = listed[1:-1] if value_type == "array" else ""
        square = (
            f"<</Type/Annot/Subtype/Square/Rect[400 400 420 420]/Metadata {_new_xmp(doc)} 0 R>>"
        )
        doc.xref_set_key(page.xref, "Annots", f"[{comments} {square}]")
    return doc.tobytes()


def _new_xmp(doc: pymupdf.Document) -> int:
    """New metadata (XMP) in `doc` that holds the name, which nothing names yet; its number."""
    xmp = _new_stream(doc, _XMP.format(name=REDACTED_NAME).encode())
    doc.xref_set_key(xmp, "Type", "/Metadata")
    doc.xref_set_key(xmp, "Subtype", "/XML")
    return xmp


def _set_metadata_bytes(doc: pymupdf.Document, written: bytes) -> None:
    """Give `doc` metadata of these bytes, as written: PyMuPDF writes text only as UTF-8."""
    stream = _new_stream(doc, written)
    doc.xref_set_key(stream, "Type", "/Metadata")
    doc.xref_set_key(stream, "Subtype", "/XML")
    doc.xref_set_key(doc.pdf_catalog(), "Metadata", f"{stream} 0 R")


def _new_stream(doc: pymupdf.Document, written: bytes) -> int:
    """A new stream in `doc` holding `written`; its number."""
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, written, new=True)
    return stream


def _add_field(
    page: pymupdf.Page,
    name: str,
    value: str,
    *,
    top: float,
    left: float = 72,
    kind: int = _TEXT_FIELD,
) -> int:
    """Add a form field `name` showing `value`, its top left at `left`, `top`; its number."""
    field = pymupdf.Widget()
    field.field_type = kind
    field.field_name = name
    if kind in (_CHOICE_FIELD, _LIST_FIELD):
        field.choice_values = [value]
    field.field_value = value
    field.rect = pymupdf.Rect(left, top, left + 228, top + 20)
    field.text_fontsize = 12
    return page.add_widget(field).xref


def _add_button(page: pymupdf.Page, caption: str) -> None:
    """Add a push button captioned `caption`, which its drawing shows."""
    button = pymupdf.Widget()
    button.field_type = _BUTTON_FIELD
    button.field_name = "mail"
    button.button_caption = caption
    button.rect = pymupdf.Rect(72, 610, 300, 640)
    page.add_widget(button)


def _stamp(
    doc: pymupdf.Document, page: pymupdf.Page, words: str, *, in_image: bool = False
) -> None:
    """Add a stamp whose drawing writes `words` only in hex, in an image if `in_image`.

    Found only string by string.
    """
    stamp = page.add_stamp_annot(pymupdf.Rect(320, 610, 548, 650), stamp=0)
    _kind, drawing = doc.xref_get_key(stamp.xref, "AP/N")
    drawn = f"BT /Helv 12 Tf 2 5 Td <{words.encode().hex()}> Tj ET"
    # Inside an image's 100 bytes, after an EI: MuPDF reads past them, another reader in them.
    if in_image:
        drawn = f"BI /W 100 /H 1 /BPC 8 /CS /G ID {f'EI {drawn}':<100} EI"
    doc.update_stream(int(drawing.split()[0]), drawn.encode())


def _sign(
    doc: pymupdf.Document, page: pymupdf.Page, *, name: str, at: tuple[float, float]
) -> int:
    """Add signature field `name`, its top left `at`, and sign it; its signature's number."""
    field = _add_signature_field(page, name=name, at=at)
    signature = doc.get_new_xref()
    signed = f"/SubFilter/adbe.pkcs7.detached/Contents<{_SIGNED_BYTES.hex()}>"
    doc.update_object(signature, f"<</Type/Sig/Filter/Adobe.PPKLite{signed}>>")
    doc.xref_set_key(field, "V", f"{signature} 0 R")
    return signature


def _add_signature_field(page: pymupdf.Page, *, name: str, at: tuple[float, float]) -> int:
    """Add signature field `name`, not signed, its top left `at`; its number."""
    left, top = at
    field = pymupdf.Widget()
    field.field_type = _SIGNATURE_FIELD
    field.field_name = name
    field.rect = pymupdf.Rect(left, top, left + 228, top + 40)
    return page.add_widget(field).xref


def _tag_a_picture(doc: pymupdf.Document, alt: str) -> None:
    """Give `doc` tags of one picture described as `alt`: all a screen reader needs of it."""
    tags = doc.get_new_xref()
    picture = doc.get_new_xref()
    doc.update_object(tags, f"<</Type/StructTreeRoot/K {picture} 0 R>>")
    doc.update_object(picture, f"<</Type/StructElem/S/Figure/P {tags} 0 R/Alt ({alt})>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{tags} 0 R")
