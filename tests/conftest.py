"""Shared fixtures for every test under tests/, however deep."""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pymupdf
import pytest
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import open_pdf, words
from squidpdf.core.fonts.catalog import FACES, face_bytes

# pytest only explains the asserts in test modules; this has it explain the helpers' too,
# so a failing assert_equal shows a diff, not two whole values. Before any test imports them.
pytest.register_assert_rewrite("tests.helpers")

_POSTSCRIPT_NAME = 6  # the font's name table entry a PDF names it by

# The sample's two pages, counted from 0 as spans count them.
REFERENCED_PAGE = 0  # fonts named but not in the file: edits use a substitute
EMBEDDED_PAGE = 1  # one font in the file, trimmed to the letters the page uses
TAGGED_LINES = ["First page", "Second page"]  # the tagged fixture's line on each page


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
    [xref] = [xref for xref, _ext, _kind, name, *_ in doc[page].get_fonts() if name == font]
    _name, _ext, _kind, buffer = doc.extract_font(xref)
    return buffer


def named_only(path: str, base_font: str, flags: int | None = None) -> str:
    """One line in a font the file only names, never stores, as Word does with Calibri.

    Written in MuPDF's Helvetica, then renamed: nothing here reads the letters'
    shapes, only the font's name and, when `flags` is given, its description.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Hello there", fontname="helv", fontsize=12)
    [(xref, *_rest)] = page.get_fonts()
    doc.xref_set_key(xref, "BaseFont", f"/{base_font}")
    if flags is not None:
        descriptor = (
            f"<</Type/FontDescriptor/FontName/{base_font}/Flags {flags}/ItalicAngle 0>>"
        )
        doc.xref_set_key(xref, "FontDescriptor", descriptor)
    doc.save(path)
    return path


def drawn_with(path: str, *, setting: str, rotate: int = 0) -> str:
    """One line in stored, trimmed Times, drawn with `setting` (e.g. "1.5 Tc") and turned.

    `setting` goes in the page's drawing just before its text: letter spacing
    (Tc) and horizontal scaling (Tz) change where each letter lands, not the
    letters, so the font still draws every one.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((72, 300), "Terms of payment", fontname="emb", fontsize=12, rotate=rotate)
    [xref] = page.get_contents()
    drawing = doc.xref_stream(xref).replace(b"BT", b"BT " + setting.encode(), 1)
    doc.update_stream(xref, drawing)
    doc.subset_fonts(verbose=False)
    doc.save(path)
    return path


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """A two-page contract with one kind of font on each page.

    Page 1 only names its fonts: "tibo" and "tiro" are MuPDF's short names for
    Times Bold and Times Roman, which it never puts in the file. Page 2 puts
    Times Roman in the file, uses it for two lines, then trims it to the
    letters those lines use, so a letter like é is left with no shape.
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

    The filled field's value is drawn by the field itself (a widget), not by the
    page, so it's read as a span like any other, but erasing the page's text
    can't reach it. That's how a filled-in PDF form comes. Inside the empty
    field the page prints a hint, as forms do: the page's own text, which an
    edit reaches.
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


@pytest.fixture
def pseudo(monkeypatch) -> str:
    """A second language to answer in, made from English, for this test only; its tag."""
    catalog = {key: pseudo_sentence(said) for key, said in words.ENGLISH_SENTENCES.items()}
    monkeypatch.setitem(words.CATALOGS, PSEUDO, catalog)
    return PSEUDO
