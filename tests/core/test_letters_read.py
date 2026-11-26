"""A page's letters are read once until the driver changes the page, and never kept past it.

A redaction asks the same page for its letters again and again, and checks what it left by
reading them after its own erase (Rule 4): letters kept past a change would hide what's left.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_type_hints

import pymupdf
import pytest

import squidpdf.core.constants as constants
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.lowlevel import PdfFile, pdf_file
from squidpdf.core.pdf.lowlevel.letters_read import LettersRead
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import CodeRun, Rect, TextRun
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_every_field_filled

_EVERYWHERE = Rect(-1e5, -1e5, 1e5, 1e5)  # past any page's edges: every letter on it
_ADDED_AT = (72.0, 400.0)  # clear of the sample's own lines
_BLACK = (0.0, 0.0, 0.0)
_A_GLYPH = b"\x00\x03"  # a two-byte code: the sample's stored Times is written by glyph

# The driver's calls that change nothing, so they may answer from the letters read.
_READS = {
    "page_count",
    "pages",
    "page_image",
    "box_images",
    "text_lines",
    "text_in",
    "letters_in",
    "drawn_boxes",
    "link_boxes",
    "drawn_glyphs",
    "drawn_glyph_boxes",
    "form_fields",
    "fonts",
    "font_bytes",
    "font_descriptor",
    "font_codes",
    "listed_widths",
    "open_font",
    "face_font",
    "hidden_copies",
    "document_hidden_copies",
    "holds_signature",
    "is_signed",
    "keeps_xmp_plain",
    "has_tags",
}

# The driver itself, past its Protocol: what it read of each page is what's checked here.
type _Driver = Any


@dataclass(frozen=True, slots=True)
class _Change:
    """A driver call that changes the document, and the page it changes; None: every page.

    `ready` runs before the letters are read, for a call that needs something there first,
    and `call` gets what it made, with a folder of the test's own.
    """

    call: Callable[[_Driver, Any, Path], object]
    page: int | None = None
    ready: Callable[[_Driver], object] = lambda _driver: None


def _added_font(driver: _Driver) -> str:
    """The resource name of a face we ship, added to the page the sample only names fonts on."""
    font = face_bytes(FACES["Carlito Regular"])
    return driver.add_font(REFERENCED_PAGE, font, resource="S1").resource


def _write_text(driver: _Driver, resource: str, _folder: Path) -> None:
    """New text on the page the sample only names fonts on, in the face added as `resource`."""
    driver.write_text(
        REFERENCED_PAGE,
        runs=[TextRun("Added", _ADDED_AT, resource)],
        size=12,
        color=_BLACK,
        opacity=1,
        scale_x=1,
        turn_ccw=0,
        letter_spacing=0,
    )


def _stored_font(driver: _Driver) -> int:
    """The object of the font the sample stores, on its second page."""
    [font] = [font for font in driver.fonts(EMBEDDED_PAGE) if font.is_embedded]
    return font.font_object


def _write_codes(driver: _Driver, _ready: None, _folder: Path) -> None:
    """A glyph written by its code in the font the sample stores."""
    driver.write_codes(
        EMBEDDED_PAGE,
        origin=_ADDED_AT,
        runs=[CodeRun(_A_GLYPH, _stored_font(driver))],
        size=12,
        color=_BLACK,
        opacity=1,
        scale_x=1,
        turn_ccw=0,
        letter_spacing=0,
    )


def _replace_font_file(driver: _Driver, _ready: None, _folder: Path) -> None:
    """The stored font's file swapped for itself: a cut-down copy keeps each glyph's number."""
    font_object = _stored_font(driver)
    driver.replace_font_file(font_object, driver.font_bytes(font_object))


def _with_xmp(driver: _Driver) -> None:
    """XMP for `drop_xmp` to delete: the sample has none."""
    driver.doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'/>")


_CHANGES = {
    "erase_text": _Change(
        lambda driver, _ready, _folder: driver.erase_text(REFERENCED_PAGE, [_EVERYWHERE]),
        page=REFERENCED_PAGE,
    ),
    "drop_links": _Change(
        lambda driver, _ready, _folder: driver.drop_links(REFERENCED_PAGE, [_EVERYWHERE]),
        page=REFERENCED_PAGE,
    ),
    "rewrite_hidden_copies": _Change(
        lambda driver, _ready, _folder: driver.rewrite_hidden_copies(
            REFERENCED_PAGE, str.upper
        ),
        page=REFERENCED_PAGE,
    ),
    "rewrite_document_hidden_copies": _Change(
        lambda driver, _ready, _folder: driver.rewrite_document_hidden_copies(str.upper)
    ),
    "drop_signatures": _Change(lambda driver, _ready, _folder: driver.drop_signatures()),
    "add_font": _Change(
        lambda driver, _ready, _folder: _added_font(driver), page=REFERENCED_PAGE
    ),
    "add_face": _Change(
        lambda driver, _ready, _folder: driver.add_face(
            REFERENCED_PAGE, FACES["Carlito Regular"], resource="S1"
        ),
        page=REFERENCED_PAGE,
    ),
    "write_text": _Change(_write_text, page=REFERENCED_PAGE, ready=_added_font),
    "write_codes": _Change(_write_codes, page=EMBEDDED_PAGE),
    "replace_font_file": _Change(_replace_font_file),
    "drop_active_content": _Change(
        lambda driver, _ready, _folder: driver.drop_active_content()
    ),
    "drop_xmp": _Change(lambda driver, _ready, _folder: driver.drop_xmp(), ready=_with_xmp),
    "drop_tags": _Change(lambda driver, _ready, _folder: driver.drop_tags()),
    "keep_pages": _Change(
        lambda driver, _ready, _folder: driver.keep_pages([EMBEDDED_PAGE, REFERENCED_PAGE])
    ),
    "turn_page": _Change(
        lambda driver, _ready, _folder: driver.turn_page(REFERENCED_PAGE, 90),
        page=REFERENCED_PAGE,
    ),
    "mark_changed_by": _Change(
        lambda driver, _ready, _folder: driver.mark_changed_by("squidpdf", datetime.now(UTC))
    ),
    "save": _Change(lambda driver, _ready, folder: driver.save(str(folder / "saved.pdf"))),
    "close": _Change(lambda driver, _ready, _folder: driver.close()),
}


def _drivers_calls() -> set[str]:
    """Every call the driver's Protocol names."""
    return {
        name
        for name, _member in inspect.getmembers(PdfDriver, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_driver_call_reads_or_is_checked_to_forget_the_letters_it_changes():
    """A call added later is one or the other, so none changes a page and keeps its letters."""
    assert_equal(_READS & set(_CHANGES), set(), "calls listed as both a read and a change")
    assert_equal(_READS | set(_CHANGES), _drivers_calls(), "the driver's calls, every one")


def _page_of(read: tuple[int, int]) -> int:
    """The page of letters read, kept by page and the flags read with."""
    page, _flags = read
    return page


def _read_every_page(driver: _Driver) -> None:
    """Every page's letters, read each way the driver reads them."""
    for page in range(driver.page_count()):
        driver.text_in(page, [_EVERYWHERE])
        driver.letters_in(page, [_EVERYWHERE])


@pytest.mark.parametrize("name", sorted(_CHANGES))
def test_a_call_that_changes_the_document_keeps_none_of_the_letters_read_before(
    pdf, tmp_path, name
):
    """Read again or not after, none of what was read of the pages it changes is kept."""
    change = _CHANGES[name]
    driver: _Driver = open_driver(pdf)
    readied = change.ready(driver)
    _read_every_page(driver)
    letters_read: LettersRead = driver.file.letters_read
    before = dict(letters_read.blocks)
    assert_equal(len(before), 2 * driver.page_count(), "letters read, each page each way")

    change.call(driver, readied, tmp_path)

    changed = [key for key in before if change.page in (None, _page_of(key))]
    # .get: a page's letters forgotten and not read again since are no longer there.
    kept = [key for key in changed if letters_read.blocks.get(key) is before[key]]
    assert_equal(kept, [], f"letters kept past {name}, by page and flags")
    if name != "close":
        driver.close()


# Each low-level part's calls that change nothing: every other one is a write, checked below.
_PART_READS = {
    "fonts": {"font_codes", "listed_widths", "inner_font", "text_font_name"},
    "glyphs": {"drawn", "boxes"},
    "additions": {"to_pdf_space"},
    "page_hidden_copies": {"read"},
    "document_hidden_copies": {"read"},
    "signatures": {"is_signed", "holds_signature"},
    "redaction": {"link_boxes"},
}


def _links_kept(file: PdfFile) -> None:
    """The page's links taken out and put back, as an erase does around its redaction."""
    with file.redaction.links_kept(REFERENCED_PAGE):
        pass


def _font_put_back(file: PdfFile) -> None:
    """The stored font named again under its resource name, as an erase puts it back."""
    [(font_object, _file_type, _kind, _name, resource, _encoding)] = file.fonts.doc[
        EMBEDDED_PAGE
    ].get_fonts()
    file.additions.restore_font(EMBEDDED_PAGE, resource, font_object)


@dataclass(frozen=True, slots=True)
class _PartWrite:
    """A low-level part's write, and the page it changes; None: every page."""

    call: Callable[[PdfFile], object]
    page: int | None


# Each low-level write, by part and call.
_PART_WRITES = {
    ("redaction", "redact"): _PartWrite(
        lambda file: file.redaction.redact(REFERENCED_PAGE, [_EVERYWHERE]),
        REFERENCED_PAGE,
    ),
    ("redaction", "links_kept"): _PartWrite(_links_kept, REFERENCED_PAGE),
    ("redaction", "drop_links"): _PartWrite(
        lambda file: file.redaction.drop_links(REFERENCED_PAGE, [_EVERYWHERE]),
        REFERENCED_PAGE,
    ),
    ("additions", "restore_font"): _PartWrite(_font_put_back, EMBEDDED_PAGE),
    ("additions", "add_opacity"): _PartWrite(
        lambda file: file.additions.add_opacity(REFERENCED_PAGE, 0.5),
        REFERENCED_PAGE,
    ),
    ("additions", "add_opacity_as_pymupdf"): _PartWrite(
        lambda file: file.additions.add_opacity_as_pymupdf(REFERENCED_PAGE, 0.5),
        REFERENCED_PAGE,
    ),
    ("additions", "add_font_as_pymupdf"): _PartWrite(
        lambda file: file.additions.add_font_as_pymupdf(
            REFERENCED_PAGE, pymupdf.Font("tiro"), resource="S1"
        ),
        REFERENCED_PAGE,
    ),
    ("additions", "add_drawing"): _PartWrite(
        lambda file: file.additions.add_drawing(REFERENCED_PAGE, b"q Q"),
        REFERENCED_PAGE,
    ),
    ("page_hidden_copies", "rewrite"): _PartWrite(
        lambda file: file.page_hidden_copies.rewrite(REFERENCED_PAGE, str.upper),
        REFERENCED_PAGE,
    ),
    ("document_hidden_copies", "rewrite"): _PartWrite(
        lambda file: file.document_hidden_copies.rewrite(str.upper),
        None,
    ),
    ("signatures", "drop"): _PartWrite(lambda file: file.signatures.drop(), None),
    ("active_content", "drop"): _PartWrite(lambda file: file.active_content.drop(), None),
    ("metadata", "mark_changed_by"): _PartWrite(
        lambda file: file.metadata.mark_changed_by("squidpdf", datetime.now(UTC)),
        None,
    ),
    ("protection", "write_entries_in_place"): _PartWrite(
        lambda file: file.protection.write_entries_in_place(),
        None,
    ),
    ("annotations", "each_its_own_object"): _PartWrite(
        lambda file: file.annotations.each_its_own_object(),
        None,
    ),
}


def _parts_calls() -> set[tuple[str, str]]:
    """Every public call of every part of a `PdfFile`, but the letters read themselves."""
    return {
        (part, name)
        for part, kind in get_type_hints(PdfFile).items()
        if part != "letters_read"
        for name, _member in inspect.getmembers(kind, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_low_level_call_reads_or_is_checked_to_forget_the_letters_it_changes():
    """Within one of the driver's calls, a read after a write sees it: each write forgets."""
    reads = {(part, name) for part, names in _PART_READS.items() for name in names}
    assert_equal(reads & set(_PART_WRITES), set(), "calls listed as both a read and a write")
    assert_equal(reads | set(_PART_WRITES), _parts_calls(), "the parts' calls, every one")


@pytest.mark.parametrize(("part", "name"), sorted(_PART_WRITES))
def test_a_low_level_write_keeps_none_of_the_letters_read_before(pdf, part, name):
    """Each write forgets where it's made: a driver's call that reads after it reads anew."""
    write = _PART_WRITES[part, name]
    doc = pymupdf.open(pdf)
    # A link, so taking the page's links out and putting them back has one to write.
    doc[REFERENCED_PAGE].insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(72, 80, 200, 100),
            "uri": "https://a.test",
        }
    )
    file = pdf_file(doc)
    for number in range(len(doc)):
        for flags in (0, 1):  # two ways of reading, as the driver reads with two sets of flags
            file.letters_read.of(number, flags, list)
    before = dict(file.letters_read.blocks)

    write.call(file)

    changed = [key for key in before if write.page in (None, _page_of(key))]
    # .get: a page's letters forgotten are no longer there.
    kept = [key for key in changed if file.letters_read.blocks.get(key) is before[key]]
    assert_equal(kept, [], f"letters kept past {part}.{name}, by page and flags")
    doc.close()


def test_an_erase_answers_with_the_letters_left_after_it(pdf):
    """It reads the page again after its own redaction: read before, every letter was there."""
    driver = open_driver(pdf)
    _read_every_page(driver)

    left = driver.erase_text(REFERENCED_PAGE, [_EVERYWHERE])

    assert_equal(left, [""], "the letters left on the page the erase cleared")
    driver.close()


def test_letters_read_that_forget_equal_fresh_ones():
    """What an open document read of its pages: forgotten, every page is read again."""
    letters_read = LettersRead()
    letters_read.of(0, 1, lambda: [{"lines": []}])
    assert_every_field_filled(letters_read, LettersRead(), "the record once a page is read")

    letters_read.forget()

    assert_equal(letters_read, LettersRead(), "the record once it forgot")


def test_only_the_last_pages_letters_read_are_kept(monkeypatch):
    """A page's letters are many objects: past the bound, the page read longest ago goes."""
    monkeypatch.setattr(constants, "LETTERS_READ_KEPT", 2)
    letters_read = LettersRead()
    for page in (0, 1, 0, 2):  # page 0 read again, so it's later than page 1
        letters_read.of(page, 1, list)

    assert_equal(sorted(letters_read.blocks), [(0, 1), (2, 1)], "pages kept, by page and flags")
