from __future__ import annotations

import io
import re
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pymupdf
import pytest
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import DriverFactory, Engine, QuarterTurn, open_pdf, words
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.documents.attached import AttachedFiles
from squidpdf.documents.store import FOLDER_STORE

# pytest rewrites asserts only in test modules: this makes a failing helper show a diff.
pytest.register_assert_rewrite("tests.helpers")

_POSTSCRIPT_NAME = 6  # the name table entry a PDF names a font by
_WIDTH_LIST_CODES = range(32, 127)
_WIDTH_LIST_EM = 1000
_POPPINS_SIZE = 14.0

REFERENCED_PAGE = 0
EMBEDDED_PAGE = 1
TAGGED_LINES = ["First page", "Second page"]
SENTENCE = (("Text is grouped into ", "helv"), ("spans", "hebo"), (" (one style).", "helv"))


@dataclass(frozen=True, slots=True)
class StoreWith:
    opens_to_write: Callable[[Path], BinaryIO] = FOLDER_STORE.open_to_write
    opens_original: Callable[[Path], Engine] = FOLDER_STORE.open_original
    touches: Callable[[Path], float] = FOLDER_STORE.touch
    sweeps: Callable[[], None] = FOLDER_STORE.sweep

    @property
    def driver_factory(self) -> DriverFactory:
        return FOLDER_STORE.driver_factory

    def create(self, owner_digest: str) -> tuple[str, Path]:
        return FOLDER_STORE.create(owner_digest)

    def find(self, doc_id: str) -> tuple[Path, str] | None:
        return FOLDER_STORE.find(doc_id)

    def open_to_write(self, folder: Path) -> BinaryIO:
        return self.opens_to_write(folder)

    def open_to_analyse(self, folder: Path, attached: AttachedFiles) -> Engine:
        return FOLDER_STORE.open_to_analyse(folder, attached)

    def delete(self, folder: Path) -> None:
        FOLDER_STORE.delete(folder)

    def open_original(self, folder: Path) -> Engine:
        return self.opens_original(folder)

    def touch(self, folder: Path) -> float:
        return self.touches(folder)

    def sweep(self) -> None:
        self.sweeps()


LOG_LINE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (INFO |WARN |ERROR) (noted  |skipped|failed ) "
    r"[a-z_.]+ [a-z_]+( [a-z_]+=\S+)*"
)
_LOG_LINE_START = re.compile(r"^\d{4}-\d\d-\d\dT", re.MULTILINE)


def log_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if _LOG_LINE_START.match(line)]


@pytest.fixture(scope="session", autouse=True)
def no_fetch() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_NO_FETCH", "1")
        yield


def write_runs(
    page: pymupdf.Page,
    runs: Sequence[tuple[str, str]],
    *,
    origin: tuple[float, float],
    size: float,
) -> list[float]:
    x, baseline = origin
    places = [x]
    for text, font in runs:
        page.insert_text((x, baseline), text, fontsize=size, fontname=font)
        x += pymupdf.get_text_length(text, fontname=font, fontsize=size)
        places.append(x)
    return places


def each_span(blocks: list[dict]) -> Iterator[dict]:
    for block in blocks:
        for line in block.get("lines", []):
            yield from line["spans"]


def each_letter(blocks: list[dict]) -> Iterator[dict]:
    for span in each_span(blocks):
        yield from span["chars"]


def cannot_cut(_subsetter: Subsetter, _font: TTFont) -> None:
    raise ValueError("fontTools can't cut this font")


def saved_as(face: str) -> str:
    font = TTFont(io.BytesIO(face_bytes(FACES[face])))
    return str(font["name"].getDebugName(_POSTSCRIPT_NAME))


def named_only(
    path: str, base_font: str, flags: int | None = None, *, italic_angle: float = 0
) -> str:
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


def inner_font_of(doc: pymupdf.Document, font_object: int) -> int:
    _kind, descendants = doc.xref_get_key(font_object, "DescendantFonts")
    return int(descendants.strip("[]").split()[0])


def name_two_byte_font(doc: pymupdf.Document, font_object: int, name: str) -> int:
    inner = inner_font_of(doc, font_object)
    for each_font in (font_object, inner):
        doc.xref_set_key(each_font, "BaseFont", f"/ABCDEF+{name}")
    return inner


def to_unicode(codespace: str, body: str) -> bytes:
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap"
        " /CMapName /Test def /CMapType 2 def"
        f" 1 begincodespacerange {codespace} endcodespacerange {body}"
        " endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()


_TYPE3_CODES = {"H": 1, "e": 2, "l": 3, "o": 4, "Y": 5, "w": 6}
TYPE3_TEXTS = ("Hello", "Yellow")


def type3_copies_at(path: str) -> str:
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


_SERIF = "Liberation Serif Regular"


def in_a_copy_lacking(
    path: str, *, text: str, lacking: str = "", stored: bool = True, turn_ccw: QuarterTurn = 0
) -> str:
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
    font = TTFont(io.BytesIO(face_bytes(FACES[face])))
    glyph_names = font["cmap"].getBestCmap()
    for letter in letters:
        name = glyph_names.get(ord(letter))
        if name is not None:
            font["glyf"][name] = TTGlyphPen(None).glyph()
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """Page 1 only names its fonts; page 2 stores Times trimmed, so é has no shape."""
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


FORM_LINE = "Name: Ada Byron"
FORM_FIELD_VALUE = "SSN 078-05-1120"
FORM_HINT = "MM/DD/YYYY"
_TEXT_FIELD = 7  # PyMuPDF's PDF_WIDGET_TYPE_TEXT, set at import, so type checkers can't see it


@pytest.fixture(scope="module")
def form() -> bytes:
    """The filled field's value is the widget's, so erasing the page's text can't reach it."""
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
    """Tagged as a real file is: enough for MuPDF to keep a page it was told to drop."""
    doc = pymupdf.open()
    for number, line in enumerate(TAGGED_LINES):
        page = doc.new_page()
        page.insert_text((72, 96), line, fontname="helv", fontsize=12)
        [contents] = page.get_contents()
        marked = b"/P <</MCID 0>> BDC\n" + doc.xref_stream(contents) + b"\nEMC"
        doc.update_stream(contents, marked)
        doc.xref_set_key(page.xref, "StructParents", str(number))
    tree, document = new_object(doc), new_object(doc)
    paragraphs = [
        new_object(doc, f"<</Type/StructElem/S/P/P {document} 0 R/Pg {page.xref} 0 R/K 0>>")
        for page in doc.pages()
    ]
    kids = " ".join(f"{paragraph} 0 R" for paragraph in paragraphs)
    doc.update_object(document, f"<</Type/StructElem/S/Document/P {tree} 0 R/K [{kids}]>>")
    by_page = " ".join(f"{n} [{paragraph} 0 R]" for n, paragraph in enumerate(paragraphs))
    parents = new_object(doc, f"<</Nums [{by_page}]>>")
    root = f"<</Type/StructTreeRoot/K {document} 0 R/ParentTree {parents} 0 R>>"
    doc.update_object(tree, root)
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{tree} 0 R")
    path = tmp_path / "tagged.pdf"
    doc.save(path)
    return str(path)


def new_object(doc: pymupdf.Document, source: str = "<<>>") -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


def new_stream(doc: pymupdf.Document, data: str | bytes, **keys: str) -> int:
    stream = new_object(doc)
    doc.update_stream(stream, data if isinstance(data, bytes) else data.encode())
    for key, value in keys.items():
        doc.xref_set_key(stream, key, value)
    return stream


@pytest.fixture
def engine(pdf):
    with open_pdf(pdf) as engine:
        yield engine


PSEUDO = "qps"  # a tag for local use: no real language will ever have it
_PLACEHOLDER = re.compile(r"(\{\w+\})")


def pseudo_sentence(english: str) -> str:
    parts = _PLACEHOLDER.split(english)
    return "".join(part if _PLACEHOLDER.fullmatch(part) else part.upper() for part in parts)


def _pseudo_entry(english: words.CatalogEntry) -> words.CatalogEntry:
    if isinstance(english, str):
        return pseudo_sentence(english)
    return {form: pseudo_sentence(each) for form, each in english.items()}


@pytest.fixture
def pseudo(monkeypatch) -> str:
    catalog = {
        key: _pseudo_entry(entry) for key, entry in words.CATALOGS[words.ENGLISH].items()
    }
    monkeypatch.setitem(words.CATALOGS, PSEUDO, catalog)
    return PSEUDO


POPPINS = Path(__file__).parent / "core" / "fonts" / "Poppins-Regular.ttf"
POPPINS_TEXT = "Hello there"


@pytest.fixture(scope="module")
def poppins_subset(tmp_path_factory) -> str:
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


REDACTED_NAME = "Ada Quill"
OTHER_LINE = "Other line"
CHOSEN = "Bob Smith"
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
NOT_XML_COPIES = ("not_xml", "not_xml_utf16", "xmp_utf32", "xmp_doctype", "xmp_deep")
SIGNED = ("signature", "certified", "dss")
# PyMuPDF's PDF_WIDGET_TYPE_* values, set at import where type checkers can't see them.
_LIST_FIELD = 4
_CHOICE_FIELD = 3
_SIGNATURE_FIELD = 6
_BUTTON_FIELD = 1
_MULTI_SELECT = 1 << 21
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
_BROKEN_XMP = "<x:xmpmeta><dc:creator>{name}</x:xmpmeta>"
_DECLARING_XMP = (
    '<!DOCTYPE x [<!ENTITY who "{name}">]>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:creator>&who;</dc:creator></x:xmpmeta>"
)
_SIGNED_BYTES = bytes(64)
# How deep `xmp_deep` nests: far past how deep Python lets calls go.
_DEEP = 3000
_NOT_XML = {
    "not_xml": _BROKEN_XMP.format(name=REDACTED_NAME).encode(),
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


def with_hidden_copies(hidden_copies: Collection[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), REDACTED_NAME, fontname="helv", fontsize=12)
    page.insert_text((72, 130), OTHER_LINE, fontname="helv", fontsize=12)
    info = {}
    if "title" in hidden_copies:
        info["title"] = f"Notes on {REDACTED_NAME}"
    if "author" in hidden_copies:
        info["author"] = REDACTED_NAME
    doc.set_metadata(info)
    if "xmp" in hidden_copies:
        doc.set_xml_metadata(_XMP.format(name=REDACTED_NAME))
    if "page_xmp" in hidden_copies:
        doc.xref_set_key(page.xref, "Metadata", f"{_new_xmp(doc)} 0 R")
    for kind, written in _NOT_XML.items():
        if kind in hidden_copies:
            _set_metadata_bytes(doc, written)
    if "bookmark" in hidden_copies:
        doc.set_toc([[1, f"Visit by {REDACTED_NAME}", 1], [1, REDACTED_NAME, 1]])
    if "note" in hidden_copies:
        note = page.add_text_annot((500, 700), f"Call {REDACTED_NAME}")
        note.set_info(title=REDACTED_NAME, subject="Visit")
        note.update()
        doc.xref_set_key(note.xref, "RC", f"(<body><p>Call {REDACTED_NAME}</p></body>)")
    if "free_text" in hidden_copies:
        page.add_freetext_annot(
            pymupdf.Rect(72, 300, 300, 320), f"Ask {REDACTED_NAME}", fontsize=12
        )
    if "stale_free_text" in hidden_copies:
        stale = page.add_freetext_annot(
            pymupdf.Rect(320, 300, 560, 320), f"Ring {REDACTED_NAME}", fontsize=12
        )
        doc.xref_set_key(stale.xref, "Contents", "(Ring)")
    if "field" in hidden_copies:
        field = _add_field(page, "who", REDACTED_NAME, top=400)
        doc.xref_set_key(field, "DV", f"({REDACTED_NAME})")
        doc.xref_set_key(field, "RV", f"(<body><p>{REDACTED_NAME}</p></body>)")
    if "undrawable_field" in hidden_copies:
        undrawable = _add_field(page, "sealed", REDACTED_NAME, top=400, left=320)
        # No kind (FT), so MuPDF can't draw it again; a viewer shows its old drawing.
        doc.xref_set_key(undrawable, "FT", "null")
    if "choice" in hidden_copies:
        choice = _add_field(page, "pick", CHOSEN, top=450, kind=_CHOICE_FIELD)
        doc.xref_set_key(choice, "Opt", f"[({CHOSEN}) [(2) ({REDACTED_NAME})]]")
    if "multi_choice" in hidden_copies:
        both = f"[({CHOSEN}) ({REDACTED_NAME})]"
        listed = _add_field(page, "team", CHOSEN, top=450, left=320, kind=_LIST_FIELD)
        doc.xref_set_key(listed, "Ff", str(_MULTI_SELECT))
        doc.xref_set_key(listed, "Opt", both)
        doc.xref_set_key(listed, "V", both)
        doc.xref_set_key(listed, "I", "[0 1]")
    if "unlisted_field" in hidden_copies:
        unlisted = _add_field(page, "signed", REDACTED_NAME, top=500)
        _kind, listed_fields = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/Fields")
        left = listed_fields.replace(f"{unlisted} 0 R", "")
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", left)
    if "tooltip" in hidden_copies:
        described = _add_field(page, "described", "", top=500, left=320)
        doc.xref_set_key(described, "TU", f"(Signature of {REDACTED_NAME})")
    if "widget_contents" in hidden_copies:
        phone = _add_field(page, "phone", "", top=700)
        doc.xref_set_key(phone, "Contents", f"(Phone of {REDACTED_NAME})")
    if "seed_value" in hidden_copies:
        to_sign = _add_signature_field(page, name="to_sign", at=(320, 660))
        signer = f"<</Cert<</SubjectDN[<</CN({REDACTED_NAME})>>]>>>>"
        doc.xref_set_key(to_sign, "SV", signer)
    if "button" in hidden_copies:
        _add_button(page, f"Email {REDACTED_NAME}")
    if "stamp" in hidden_copies:
        _stamp(doc, page, f"Seen by {REDACTED_NAME}")
    if "stamp_in_image" in hidden_copies:
        _stamp(doc, page, f"Seen by {REDACTED_NAME}", in_image=True)
    if "signature" in hidden_copies:
        _sign(doc, page, name="signed_by", at=(72, 550))
    if "certified" in hidden_copies:
        certifying = _sign(doc, page, name="certified_by", at=(320, 550))
        doc.xref_set_key(doc.pdf_catalog(), "Perms", f"<</DocMDP {certifying} 0 R>>")
    if "dss" in hidden_copies:
        _sign(doc, page, name="kept_checkable", at=(72, 660))
        certificate = new_stream(doc, _SIGNED_BYTES)
        by_signature = f"/VRI<</{'A' * 40} <</Cert[{certificate} 0 R]>>>>"
        doc.xref_set_key(
            doc.pdf_catalog(), "DSS", f"<</Certs[{certificate} 0 R]{by_signature}>>"
        )
    if "tag" in hidden_copies:
        _tag_a_picture(doc, f"Photo of {REDACTED_NAME}")
    if "annotation_xmp" in hidden_copies:
        value_type, listed = doc.xref_get_key(page.xref, "Annots")
        comments = listed[1:-1] if value_type == "array" else ""
        square = (
            f"<</Type/Annot/Subtype/Square/Rect[400 400 420 420]/Metadata {_new_xmp(doc)} 0 R>>"
        )
        doc.xref_set_key(page.xref, "Annots", f"[{comments} {square}]")
    return doc.tobytes()


def _new_xmp(doc: pymupdf.Document) -> int:
    return new_stream(
        doc, _XMP.format(name=REDACTED_NAME).encode(), Type="/Metadata", Subtype="/XML"
    )


def _set_metadata_bytes(doc: pymupdf.Document, written: bytes) -> None:
    stream = new_stream(doc, written, Type="/Metadata", Subtype="/XML")
    doc.xref_set_key(doc.pdf_catalog(), "Metadata", f"{stream} 0 R")


def _add_field(
    page: pymupdf.Page,
    name: str,
    value: str,
    *,
    top: float,
    left: float = 72,
    kind: int = _TEXT_FIELD,
) -> int:
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
    button = pymupdf.Widget()
    button.field_type = _BUTTON_FIELD
    button.field_name = "mail"
    button.button_caption = caption
    button.rect = pymupdf.Rect(72, 610, 300, 640)
    page.add_widget(button)


def _stamp(
    doc: pymupdf.Document, page: pymupdf.Page, words: str, *, in_image: bool = False
) -> None:
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
    field = _add_signature_field(page, name=name, at=at)
    signature = doc.get_new_xref()
    signed = f"/SubFilter/adbe.pkcs7.detached/Contents<{_SIGNED_BYTES.hex()}>"
    doc.update_object(signature, f"<</Type/Sig/Filter/Adobe.PPKLite{signed}>>")
    doc.xref_set_key(field, "V", f"{signature} 0 R")
    return signature


def _add_signature_field(page: pymupdf.Page, *, name: str, at: tuple[float, float]) -> int:
    left, top = at
    field = pymupdf.Widget()
    field.field_type = _SIGNATURE_FIELD
    field.field_name = name
    field.rect = pymupdf.Rect(left, top, left + 228, top + 40)
    return page.add_widget(field).xref


def _tag_a_picture(doc: pymupdf.Document, alt: str) -> None:
    tags = doc.get_new_xref()
    picture = doc.get_new_xref()
    doc.update_object(tags, f"<</Type/StructTreeRoot/K {picture} 0 R>>")
    doc.update_object(picture, f"<</Type/StructElem/S/Figure/P {tags} 0 R/Alt ({alt})>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{tags} 0 R")
