from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import time
import zlib
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from functools import partial
from itertools import accumulate
from pathlib import Path
from typing import Any, cast

import pymupdf
import pytest
from fastapi.testclient import TestClient
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

from squidpdf.api.pool import WorkerPool, start_pool
from squidpdf.core import Engine, Message, Param, Rect, words, write_dense
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.documents import constants as documents_constants, store
from squidpdf.editing import constants as editing_constants, export
from tests.api.conftest import (
    InProcess,
    MovingAfterAnErase,
    Place,
    around,
    drawn_page,
    render,
    span_starting,
    upload,
    verdicts_of,
)
from tests.conftest import (
    CHOSEN,
    NAME_COPIES,
    NOT_XML_COPIES,
    OTHER_LINE,
    REDACTED_NAME,
    SIGNED,
    TAGGED_LINES,
    StoreWith,
    cannot_cut,
    each_letter,
    named_with_widths,
    new_object,
    new_stream,
    with_hidden_copies,
)
from tests.helpers import (
    assert_at_least,
    assert_at_most,
    assert_equal,
    assert_false,
    assert_in,
    assert_not_in,
    assert_problem,
    assert_true,
)

_SKIPPED = "Squid-Skipped-Edits"
_NOTICES = "Squid-Notices"
_HEADER = "CONFIDENTIAL"
_LINES = ["First page", "Second page", "Third page"]
_OPENING = "This agreement is made on"
_UNTIMED_S = 600  # time enough for any analysis: an upload isn't what's timed here
_NUMBER = "4111 1111 1111 1111"
# Its line, drawn with an fi ligature; its "US" is in the kept line's "en-US" too.
_CARD_LINE = f"A US card on \ufb01le: {_NUMBER}"
_CARD = f"A US card on file {_NUMBER}"
_KEPT = "Keep this line"
_SCAN = f"{_CARD.lower().replace(' ', '_')}.png"
_SQUARE = "10 10 20 20 re f"  # a filled square, drawn inside marked content to keep it
_AROUND_SQUARE = f"/Span <</ActualText ({_CARD})>> BDC {_SQUARE} EMC"
_LIGATURES = (
    "<</Type/Font/Subtype/Type1/BaseFont/Helvetica"
    "/Encoding<</Type/Encoding/BaseEncoding/WinAnsiEncoding/Differences[1/fi]>>>>"
)
# Its end of data (>) against its EI.
_HEX_IMAGE = "BI /W 1 /H 1 /BPC 8 /CS /G /F /AHx ID 00>EI"
# Its 16 raw bytes hold " EI (" and end in an E an I follows: only their count ends them.
_UNFILTERED_IMAGE = "BI /W 4 /H 4 /BPC 8 /CS /G ID\r\n\0 EI (" + "\0" * 9 + "EI ( EI"
_ZLIB_HEADER = b"\x78\x01"
_FORM = {"Type": "/XObject", "Subtype": "/Form", "BBox": "[0 0 100 100]"}
_TILING = {
    "PatternType": "1",
    "PaintType": "1",
    "TilingType": "1",
    "BBox": "[0 0 20 20]",
    "XStep": "20",
    "YStep": "20",
    "Resources": "<<>>",
}
_TYPE3 = (
    "<</Type/Font/Subtype/Type3/FontBBox[0 0 1000 1000]/FontMatrix[0.001 0 0 0.001 0 0]"
    "/Encoding<</Type/Encoding/Differences[120/x]>>/FirstChar 120/LastChar 120/Widths[1000]"
    "/CharProcs<</x {letter} 0 R>>>>"
)
_PLACES = (
    "drawing",
    "point",
    "properties",
    "form",
    "pattern",
    "smask",
    "type3",
    "annotation",
    "nul",
    "nul_reference",
    "dictionary_first",
    "twice",
    "twice_hex",
)
_TRAILER = -1  # PyMuPDF's xref for the file's trailer
_HIDDEN_COPY = re.compile(r"/(ActualText|Alt|E)\s*\(([^)]*)\)")


_STALL_S = 60  # far past any export timeout here
_STALLED_TIMEOUT_S = 3  # long enough to start the export, short enough to wait for


def _stalling_driver(path: str) -> PdfDriver:
    time.sleep(_STALL_S)
    return open_driver(path)


@pytest.fixture
def own_pool(server: TestClient) -> Iterator[WorkerPool]:
    if server.portal is None:
        pytest.fail("the app isn't started")
    pool = server.portal.call(start_pool)
    yield pool
    server.portal.call(pool.close)


@pytest.fixture(scope="module")
def three_pages() -> bytes:
    """The header repeats, so a redaction read on the wrong page finds it and fails."""
    doc = pymupdf.open()
    for line in _LINES:
        page = doc.new_page()
        page.insert_text((72, 72), _HEADER, fontname="helv", fontsize=10)
        page.insert_text((72, 100), line, fontname="helv", fontsize=12)
    return doc.tobytes()


@pytest.fixture
def three(mine, three_pages) -> dict:
    return upload(mine, three_pages).json()


def _redact(span: dict) -> dict:
    return {"kind": "redact", "span_id": span["id"]}


def _new_text_at(span: dict, text: str, origin: list[float]) -> dict:
    return {"kind": "insert", "page": span["page"], "origin": origin, "text": text, "size": 11}


def _over(span: dict, text: str) -> dict:
    return _new_text_at(span, text, [span["bbox"]["x0"] + 40, span["bbox"]["y1"]])


def _export(
    client,
    doc: dict,
    edits: list[dict],
    *,
    pages: list[int] | None = None,
    language: str = "en",
):
    body = {"edits": edits} if pages is None else {"edits": edits, "pages": pages}
    headers = {"Accept-Language": language}
    return client.post(f"/api/documents/{doc['id']}/export", json=body, headers=headers)


def _opened(response) -> pymupdf.Document:
    kind = response.headers["content-type"]
    assert_equal((response.status_code, kind), (200, "application/pdf"), response.text[:200])
    return pymupdf.open(stream=response.content, filetype="pdf")


def _info(pdf: pymupdf.Document, key: str) -> str:
    return pdf.xref_get_key(_TRAILER, f"Info/{key}")[1]


def _changed_at(pdf: pymupdf.Document) -> datetime:
    return datetime.strptime(_info(pdf, "ModDate"), "D:%Y%m%d%H%M%SZ").replace(tzinfo=UTC)


def _files(doc: dict) -> list[str]:
    folder = store.root() / doc["id"]
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def _lines(pdf: pymupdf.Document) -> list[list[str]]:
    return [page.get_text().splitlines() for page in pdf.pages()]


def _page_objects(pdf: pymupdf.Document) -> int:
    types = [pdf.xref_get_key(xref, "Type")[1] for xref in range(1, pdf.xref_length())]
    return types.count("/Page")


def test_a_face_that_could_not_be_cut_down_is_said_in_a_header_and_the_file_still_comes(
    app, mine, doc, monkeypatch, pseudo
):
    monkeypatch.setattr(app.state, "pool", InProcess())
    monkeypatch.setattr(Subsetter, "subset", cannot_cut)
    monkeypatch.setitem(words.CATALOGS[pseudo], "face_not_trimmed", "{font} ENTIÈRE")
    span = span_starting(doc, 0, "Made")  # its font is only named, so a face we ship redraws it
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [edit], language=pseudo)

    assert_in(span["text"], _lines(_opened(response))[0], "the redrawn page's lines")
    said = response.headers[_NOTICES]
    assert_true(said.isascii(), f"the header is ASCII: {said!r}")
    font = "Liberation Serif Regular"
    expected = [
        {
            "kind": "file",
            "detail": f"{font} ENTIÈRE",
            "code": "face_not_trimmed",
            "params": {"font": font},
        }
    ]
    assert_equal(json.loads(said), expected, "what came out other than asked")
    assert_equal(response.headers["Content-Language"], pseudo, "the language it's said in")


@dataclass(frozen=True, slots=True, eq=False)
class _NotErasingDriver:
    driver: PdfDriver

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        return ["" for _box in boxes]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _not_erasing(path: str) -> PdfDriver:
    return cast(PdfDriver, _NotErasingDriver(open_driver(path)))


@pytest.mark.parametrize("pages", [None, [1, 0]], ids=["every page", "pages moved"])
def test_a_redaction_the_check_cannot_confirm_downloads_nothing(
    app, mine, doc, monkeypatch, pages
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_not_erasing))
    span = span_starting(doc, 1, "Invoices")
    covered = span_starting(doc, 1, "Delivery")
    kept = _files(doc)

    edits = [
        _redact(covered),
        _over(covered, "March"),
        _redact(span),
        _over(span, "Hello there"),
    ]
    response = _export(mine, doc, edits, pages=pages)

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=span["text"], page=2)
    assert_equal(response.json()["detail"], said, "what the user reads")
    assert_equal(_files(doc), kept, "the document's files after the export")


_TWO_NUMBERS_LINE = "Card 4421 then 9918 ends"
_FIRST_NUMBER = range(5, 9)
_SECOND_NUMBER = range(15, 19)


def _with_a_card_line() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), _TWO_NUMBERS_LINE, fontname="helv", fontsize=12)
    doc.set_metadata({"title": _TWO_NUMBERS_LINE})
    return doc.tobytes()


@pytest.fixture(scope="module")
def marked() -> bytes:
    return _marked_pdf(_PLACES)


def _stored_flate(*blocks: bytes) -> bytes:
    stored = [_stored_block(block, last=False) for block in blocks]
    checksum = zlib.adler32(b"".join(blocks)).to_bytes(4, "big")
    return _ZLIB_HEADER + b"".join(stored) + _stored_block(b"", last=True) + checksum


def _stored_block(block: bytes, *, last: bool) -> bytes:
    size = len(block)
    return (
        bytes([last])
        + size.to_bytes(2, "little")
        + (size ^ 0xFFFF).to_bytes(2, "little")
        + block
    )


def _a85_holding_end() -> str:
    """Its ASCII85 pixels spell an EI and the card line."""
    text = f"EI\n({_CARD})"
    pixels = base64.a85decode(text.encode(), ignorechars=b" \n")
    image = f"BI /W {len(pixels)} /H 1 /BPC 8 /CS /G /F /A85 ID {text}~> EI"
    return f"/Figure <</Alt (A figure)>> BDC {image} EMC"


@dataclass(frozen=True, slots=True)
class _Tile:
    pattern: str
    drawing: str | bytes


_LINE_IN_UTF16_HEX = f"<FEFF{_CARD.encode('utf-16-be').hex()}>"
_LINE_IN_UTF16_OCTAL = "".join(f"\\{byte:03o}" for byte in f"﻿{_CARD}".encode("utf-16-be"))
_AFTER_AN_EI = f" EI[ ] pop {_AROUND_SQUARE} ".encode()
_TILES = {
    # A dictionary MuPDF can't read but draws on past.
    "pattern": _Tile(
        "P0", f"/Span <</ActualText (Logo) /Bad>> BDC {_SQUARE} EMC {_AROUND_SQUARE}"
    ),
    # A NUL, where MuPDF stops reading.
    "nul": _Tile("P3", f"/Span <</ActualText (Ref\\000{_CARD})>> BDC {_SQUARE} EMC"),
    # The dictionary before its tag, which MuPDF reads as if after.
    "dictionary_first": _Tile("P5", f"<</ActualText ({_CARD})>> /Span BDC {_SQUARE} EMC"),
    # ActualText written twice, the line first: MuPDF keeps "Logo".
    "twice": _Tile(
        "P6", f"/Span <</ActualText ({_CARD}) /ActualText (Logo)>> BDC {_SQUARE} EMC"
    ),
    # The same in hex, so no ( says a string is there.
    "twice_hex": _Tile(
        "P22",
        f"/Span <</ActualText <{_CARD.encode().hex()}> /ActualText <{b'Logo'.hex()}>>>"
        f" BDC {_SQUARE} EMC",
    ),
    # Alone from here: a dictionary MuPDF can't read, so a check must read its text.
    "unreadable": _Tile(
        "P1", f"/Span <</ActualText {_LINE_IN_UTF16_HEX} /Bad>> BDC {_SQUARE} EMC"
    ),
    # MuPDF stops reading at a key that's a string, before the line.
    "unreadable_first": _Tile(
        "P2", f"/Span << (Logo) /ActualText ({_CARD}) >> BDC {_SQUARE} EMC"
    ),
    # MuPDF's parser stops quietly before the line, at an ID.
    "unreadable_id": _Tile(
        "P7", f"/Span <</ActualText (Logo) ID /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
    ),
    # A >> in an array before the line, which a count takes as the end.
    "unreadable_array": _Tile(
        "P8", f"/Span <</A [ >> ] /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
    ),
    # A key that's a string after it: both readings stop before the line.
    "unreadable_both": _Tile(
        "P9", f"/Span <</A [ >> ] (junk) /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
    ),
    # No EI after its byte: MuPDF never reaches the line.
    "unreadable_image": _Tile("P4", f"BI /W 1 /H 1 /BPC 8 /CS /G ID \0 {_AROUND_SQUARE}"),
    # MuPDF reads the % as a comment over the line; a reader ending at the first EI reads it.
    "unreadable_image_comment": _Tile(
        "P10", f"BI /W 100 /H 1 /BPC 8 /CS /G ID %EI {_AROUND_SQUARE}{' ' * 100}"
    ),
    # MuPDF reads one string from the (, the line in UTF-16 octal inside it.
    "unreadable_image_string": _Tile(
        "P11",
        "BI /W 100 /H 1 /BPC 8 /CS /G ID (EI "
        f"/Span <</ActualText ({_LINE_IN_UTF16_OCTAL})>> BDC {_SQUARE} EMC{' ' * 100}",
    ),
    # MuPDF reads one hex string from the < past the line's, digits out of step.
    "unreadable_image_hex": _Tile(
        "P12",
        "/Span <</X BI /W 1 /H 1 /BPC 8 /CS /G ID <EI"
        f" /ActualText {_LINE_IN_UTF16_HEX}>> BDC {_SQUARE} EMC",
    ),
    # A reader ending the image at its first EI reads the line as marked content.
    "image_bytes": _Tile(
        "P13", f"BI /W 200 /H 1 /BPC 8 /CS /G ID {f'EI {_AROUND_SQUARE}':<200} EI"
    ),
    # Its string reads only as UTF-16; a reader ending the image at the EI reads the line plain.
    "plain_in_utf16": _Tile(
        "P14",
        f"BI /W 100 /H 1 /BPC 8 /CS /G ID (\\376\\377 EI {_AROUND_SQUARE}){' ' * 100}",
    ),
    # MuPDF looks on for an EI after a space; a reader taking the first EI reads the line.
    "image_end_bracket": _Tile(
        "P15", f"BI /W 1 /H 1 /BPC 8 /CS /G ID \0 EI[ ] pop {_AROUND_SQUARE} EI"
    ),
    # MuPDF reads every Flate block; a reader stopping at the dot reads on.
    "image_flate_tail": _Tile(
        "P17",
        b"BI /W 1 /H 1 /BPC 8 /CS /G /F /Fl ID " + _stored_flate(b"\0", _AFTER_AN_EI) + b" EI",
    ),
    # MuPDF and Poppler read /Filter; /F's ASCII85 count would reach past the line.
    "image_both_keys": _Tile(
        "P20",
        b"BI /W 100 /H 1 /BPC 8 /CS /G /F /A85 /Filter /Fl ID "
        + _stored_flate(b"A" * 100, _AFTER_AN_EI)
        + b" EI",
    ),
    # A reader takes non-hex letters as 0s; a count of hex digits alone runs past the line.
    "image_hex_letters": _Tile(
        "P21",
        f"BI /W 4 /H 1 /BPC 8 /CS /G /F /AHx ID 00gggggg00EI[ ] pop {_AROUND_SQUARE} > EI",
    ),
    # MuPDF skips CR and LF, so its dots end before "I"; a reader skipping one, before "EI[".
    "image_crlf": _Tile(
        "P18", f"BI /W 2 /H 1 /BPC 8 /CS /G ID\r\n\0EI[ ] pop {_AROUND_SQUARE} EI"
    ),
    # An EI with bytes no drawing has after it, as compressed bytes may: no reader ends there.
    "image_holding_end": _Tile(
        "P16",
        "/Figure <</Alt (A figure)>> BDC "
        f"BI /W 100 /H 1 /BPC 8 /CS /G ID {f'EI \xff ({_CARD}) \xff':<100} EI EMC",
    ),
    # A reader ends ASCII85 at its ~>, or once it has its pixels, so the line is only pixels.
    "a85_holding_end": _Tile("P19", _a85_holding_end()),
}


def _tiled(doc: pymupdf.Document, resources: int, tile: _Tile) -> list[str]:
    stream = new_stream(doc, tile.drawing, **_TILING)
    doc.xref_set_key(resources, f"Pattern/{tile.pattern}", f"{stream} 0 R")
    return [f"/Pattern cs /{tile.pattern} scn {_SQUARE}"]


def _in_an_e(_doc: pymupdf.Document, _page: pymupdf.Page, _resources: int) -> list[str]:
    return [f"/Span <</E ({_CARD})>> BDC {_SQUARE} EMC"]


def _in_a_point(_doc: pymupdf.Document, _page: pymupdf.Page, _resources: int) -> list[str]:
    return [f"/Span <</ActualText ({_CARD})>> DP"]


def _in_properties(doc: pymupdf.Document, _page: pymupdf.Page, resources: int) -> list[str]:
    doc.xref_set_key(resources, "Properties/MC0", f"<</ActualText ({_CARD})>>")
    return [f"/Span /MC0 BDC {_SQUARE} EMC"]


def _in_a_form(doc: pymupdf.Document, _page: pymupdf.Page, resources: int) -> list[str]:
    alt = f"/Figure <</Alt (A scan: {_SCAN})>> BDC {_SQUARE} EMC"
    figure = new_stream(doc, alt, **_FORM)
    doc.xref_set_key(resources, "XObject/Fm0", f"{figure} 0 R")
    return ["/Fm0 Do"]


def _in_a_soft_mask(doc: pymupdf.Document, _page: pymupdf.Page, resources: int) -> list[str]:
    group = new_stream(doc, _AROUND_SQUARE, **_FORM, Group="<</S/Transparency>>")
    mask = f"<</SMask<</S/Luminosity/G {group} 0 R>>>>"
    doc.xref_set_key(resources, "ExtGState/GS0", mask)
    return [f"q /GS0 gs {_SQUARE} Q"]


def _in_a_type3_letter(doc: pymupdf.Document, _page: pymupdf.Page, resources: int) -> list[str]:
    letter = new_stream(doc, f"1000 0 d0 {_HEX_IMAGE} {_AROUND_SQUARE}")
    font = new_object(doc, _TYPE3.format(letter=letter))
    doc.xref_set_key(resources, "Font/T3", f"{font} 0 R")
    return ["BT /T3 12 Tf 72 500 Td (x) Tj ET"]


def _in_an_annotation(doc: pymupdf.Document, page: pymupdf.Page, _resources: int) -> list[str]:
    annotation = page.add_rect_annot(pymupdf.Rect(300, 300, 400, 330))
    _kind, appearance = doc.xref_get_key(annotation.xref, "AP/N")
    appearance_drawing = f"{_UNFILTERED_IMAGE} {_AROUND_SQUARE}"
    doc.update_stream(int(appearance.split()[0]), appearance_drawing.encode())
    return []


def _in_a_string_apart(doc: pymupdf.Document, _page: pymupdf.Page, resources: int) -> list[str]:
    string = new_object(doc, f"(Ref\\000{_CARD})")
    doc.xref_set_key(resources, "Properties/MC1", f"<</ActualText {string} 0 R>>")
    return [f"/Span /MC1 BDC {_SQUARE} EMC"]


_ELSEWHERE: dict[str, Callable[[pymupdf.Document, pymupdf.Page, int], list[str]]] = {
    "drawing": _in_an_e,
    "point": _in_a_point,
    "properties": _in_properties,
    "form": _in_a_form,
    "smask": _in_a_soft_mask,
    "type3": _in_a_type3_letter,
    "annotation": _in_an_annotation,
    "nul_reference": _in_a_string_apart,
}


def _marked_pdf(places: Collection[str]) -> bytes:
    """Most wrap a square: MuPDF's erase drops marked content it empties, and its Properties."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="helv")
    _kind, resources = doc.xref_get_key(page.xref, "Resources")
    resources_xref = int(resources.split()[0])
    doc.xref_set_key(resources_xref, "Font/Lig", _LIGATURES)
    number = f"({_NUMBER}) Tj"
    if "drawing" in places:
        number = f"/Span <</ActualText ({_NUMBER})>> BDC {number} EMC"
    kept = f"<</Lang (en-US) /ActualText ({_KEPT})>>"
    drawing = [f"/Span {kept} BDC BT /helv 12 Tf 72 700 Td ({_KEPT}) Tj ET EMC"]
    for place in places:
        if place in _TILES:
            drawing += _tiled(doc, resources_xref, _TILES[place])
            continue
        drawing += _ELSEWHERE[place](doc, page, resources_xref)
    drawing.append(f"BT /Lig 12 Tf 72 600 Td (A US card on \\001le: ) Tj {number} ET")
    contents = new_stream(doc, "\n".join(drawing))
    doc.xref_set_key(page.xref, "Contents", f"{contents} 0 R")
    return doc.tobytes()


# A save's id is random and its dates the clock's: either can spell any digits.
_SAVES_OWN = re.compile(r"/ID\s*\[[^\]]*\]|/(?:CreationDate|ModDate)\s*\([^)]*\)")


def _everything_in(pdf: pymupdf.Document) -> str:
    return _SAVES_OWN.sub(
        "",
        " ".join(
            f"{pdf.xref_object(xref)} {pdf.xref_stream(xref) or b''!r}"
            for xref in range(1, pdf.xref_length())
        ),
    )


def _hidden_copies_in(pdf: pymupdf.Document) -> list[tuple[str, str]]:
    # A set: an object packed in an object stream is read twice, alone and in the stream.
    return sorted(set(_HIDDEN_COPY.findall(_everything_in(pdf))))


@dataclass(frozen=True, slots=True, eq=False)
class _KeepingDriver:
    driver: PdfDriver

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None: ...

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None: ...

    def drop_signatures(self) -> None: ...

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _keeping_hidden_copies(path: str) -> PdfDriver:
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _KeepingDriver(open_driver(path)))


def test_a_redaction_leaves_no_hidden_copy_of_its_words_on_the_page(mine, marked):
    doc = upload(mine, marked).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    saved = _opened(response)
    expected = [
        ("ActualText", _KEPT),
        ("ActualText", "Logo"),
        ("ActualText", "Ref"),
        ("Alt", "A scan: .png"),
    ]
    assert_equal(_hidden_copies_in(saved), expected, "the hidden copies left in the file")
    assert_in("(en-US)", _everything_in(saved), "the kept line's language, no hidden copy")
    assert_not_in(_NUMBER, _everything_in(saved), "the card number, anywhere in the file")
    assert_equal(_lines(saved), [[_KEPT, "x"]], "the page's lines, the Type3 letter's after")


@pytest.mark.parametrize(
    "place",
    [
        *_PLACES,
        "unreadable",
        "unreadable_first",
        "unreadable_id",
        "unreadable_array",
        "unreadable_both",
        "unreadable_image",
        "unreadable_image_comment",
        "unreadable_image_string",
        "unreadable_image_hex",
        "image_bytes",
        "plain_in_utf16",
        "image_end_bracket",
        "image_flate_tail",
        "image_crlf",
        "image_both_keys",
        "image_hex_letters",
    ],
)
def test_a_hidden_copy_still_in_the_saved_file_downloads_nothing(app, mine, monkeypatch, place):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_keeping_hidden_copies))
    doc = upload(mine, _marked_pdf([place])).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=_CARD_LINE, page=1)
    assert_equal(response.json()["detail"], said, "what the user reads")


@pytest.mark.parametrize("place", ["image_holding_end", "a85_holding_end"])
def test_an_image_whose_bytes_only_hold_an_ei_still_downloads(mine, place):
    doc = upload(mine, _marked_pdf([place])).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    assert_equal(_lines(_opened(response)), [[_KEPT]], "the page's lines")


@pytest.mark.parametrize("new_text", [[], ["card"]], ids=["alone", "under new text"])
def test_a_hidden_copy_the_redaction_cannot_rewrite_is_warned_at_render_and_refused_at_export(
    mine, new_text
):
    doc = upload(mine, _marked_pdf(["unreadable"])).json()
    card = span_starting(doc, 0, "A US card")
    edits = [_redact(card), *(_over(card, text) for text in new_text)]

    rendered = render(mine, doc, edits, [around(card)], scale=1).json()
    exported = _export(mine, doc, edits)

    said = words.render(Message("not_redacted", {"text": card["text"], "page": 1}))
    expected = {"verified": False, "hidden_copies": ["screen_reader_text"], "message": said}
    assert_equal(verdicts_of(rendered), {card["id"]: expected}, "what render says of it")
    notices = [(notice["span_id"], notice["code"]) for notice in rendered["notices"]]
    assert_equal(notices, [(card["id"], "not_redacted")], "render's notices")
    assert_problem(exported, "redaction_failed", 422)


@pytest.mark.parametrize(
    ("turns", "origin_of"),
    [
        ([], lambda box: [box["x0"] + 40, box["y1"]]),
        (
            [{"kind": "rotate_page", "page": 0, "degrees": 180}],
            lambda box: [box["x0"] + 40, box["y0"]],
        ),
    ],
    ids=["a level page", "a page an edit turns"],
)
def test_new_text_over_a_redaction_sharing_a_word_with_it_is_named_at_render_and_export(
    mine, doc, turns, origin_of
):
    made = span_starting(doc, 0, "Made on")
    edits = [*turns, _redact(made), _new_text_at(made, "on", origin_of(made["bbox"]))]

    exported = _export(mine, doc, edits)
    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()

    assert_problem(exported, "redaction_covered", 422)
    said = words.sentence("redaction_covered").format(page=1)
    assert_equal(exported.json()["detail"], said, "what the user reads")
    covered = words.sentence("covered_by_new_text")
    assert_equal(rendered["redactions"][made["id"]]["message"], covered, "what render says")
    notices = [(notice["span_id"], notice["code"]) for notice in rendered["notices"]]
    assert_equal(notices, [(made["id"], "covered_by_new_text")], "render's notices")


def test_new_text_over_a_redaction_sharing_no_word_with_it_downloads_without_the_redaction(
    mine, doc
):
    made = span_starting(doc, 0, "Made on")
    response = _export(mine, doc, [_redact(made), _over(made, "Hello there")])

    first_page = _lines(_opened(response))[0]
    assert_equal(first_page, ["SERVICES AGREEMENT", "Hello there"], "the first page's lines")


def test_a_redaction_leaves_no_copy_of_its_words_anywhere_the_file_keeps_one(mine):
    doc = upload(mine, with_hidden_copies(NAME_COPIES)).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    response = _export(mine, doc, [_redact(name)])

    saved = _opened(response)
    expected = {
        "title": ("string", "Notes on"),
        "author": ("null", "null"),
        "xmp": ["Notes on"],
        "page_xmp": ["Notes on"],
        "annotation_xmp": ["Notes on"],
        "bookmark": (("string", "Visit by"), ("string", "")),
        "note": ("Call", "", "Visit", ("null", "null")),
        "free_text": "Ask",
        "stale_free_text": ("Ring", "Ring"),
        "field": ("", ("null", "null"), ("null", "null")),
        "undrawable_field": (("string", ""), ("null", "null")),
        "choice": (CHOSEN, ("array", f"[({CHOSEN})]")),
        "multi_choice": (
            ("array", f"[({CHOSEN})]"),
            ("array", f"[({CHOSEN})]"),
            ("null", "null"),
        ),
        "unlisted_field": "",
        "tooltip": ("string", "Signature of"),
        "widget_contents": ("string", "Phone of"),
        "seed_value": ("null", "null"),
        "button": ("dict", "<</CA(Email)>>"),
        "stamp": ("null", "null"),
        "tag": ("string", "Photo of"),
    }
    assert_equal(_kept_in(saved), expected, "what each place holds")
    assert_not_in("Quill", _everything_in(saved), "the name, anywhere in the file")
    expected_lines = [[OTHER_LINE, "Ask", "Ring", "APPROVED", CHOSEN, CHOSEN, "SIGN", "Email"]]
    assert_equal(
        _lines(saved), expected_lines, "the page's lines, the comments' and the fields'"
    )


@pytest.mark.parametrize("hidden_copy", [*NAME_COPIES, *NOT_XML_COPIES, "stamp_in_image"])
def test_each_hidden_copy_a_redaction_leaves_in_the_file_downloads_nothing(
    app, mine, monkeypatch, hidden_copy
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_keeping_hidden_copies))
    doc = upload(mine, with_hidden_copies([hidden_copy])).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    rendered = render(mine, doc, [_redact(name)], [around(name)], scale=1).json()
    response = _export(mine, doc, [_redact(name)])

    verified = rendered["redactions"][name["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=REDACTED_NAME, page=1)
    assert_equal(response.json()["detail"], said, "what the user reads")


@pytest.mark.parametrize("hidden_copy", NOT_XML_COPIES)
def test_metadata_that_is_not_xml_goes_whole_once_it_holds_a_redacted_word(mine, hidden_copy):
    doc = upload(mine, with_hidden_copies([hidden_copy])).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    response = _export(mine, doc, [_redact(name)])

    saved = _opened(response)
    metadata = saved.xref_get_key(saved.pdf_catalog(), "Metadata")
    assert_equal(metadata, ("null", "null"), "the metadata stream the catalog names")
    assert_not_in("Quill", _everything_in(saved), "the name, anywhere in the file")


@pytest.mark.parametrize("signed", SIGNED)
def test_a_redaction_in_a_signed_document_takes_every_signature_away(mine, signed):
    doc = upload(mine, with_hidden_copies([signed])).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    rendered = render(mine, doc, [_redact(name)], [around(name)], scale=1).json()
    response = _export(mine, doc, [_redact(name)])

    said = words.sentence("signatures_removed")
    told = {"verified": True, "hidden_copies": ["signatures"], "message": said}
    assert_equal(verdicts_of(rendered), {name["id"]: told}, "what render says of it")
    expected = (["null"], "null", "null")
    assert_equal(_signatures_in(_opened(response)), expected, "the signatures, Perms and DSS")


def test_a_signed_document_exported_with_no_redaction_keeps_its_signatures(mine):
    doc = upload(mine, with_hidden_copies(SIGNED)).json()
    line = span_starting(doc, 0, OTHER_LINE)
    edits = [{"kind": "replace", "span_id": line["id"], "text": "Other lane"}]

    edited = _export(mine, doc, edits)
    downloaded = _export(mine, doc, [])

    expected = (["xref"] * 3, "dict", "dict")
    assert_equal(_signatures_in(_opened(edited)), expected, "the edited file's signatures")
    assert_equal(_signatures_in(_opened(downloaded)), expected, "the downloaded file's")


def _signatures_in(pdf: pymupdf.Document) -> tuple[list[str], str, str]:
    """A signature reads "xref"; a key the file leaves out, "null"."""
    catalog = pdf.pdf_catalog()
    values = [pdf.xref_get_key(field.xref, "V")[0] for field in pdf[0].widgets()]
    return values, pdf.xref_get_key(catalog, "Perms")[0], pdf.xref_get_key(catalog, "DSS")[0]


_TYPED_CHANGED_AT = "2024-05-04T09:00:00Z"
_TYPED_PRODUCER = "Acrobat Distiller"
_TYPED_XMP = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    ' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"'
    ' xmlns:xmpTPg="http://ns.adobe.com/xap/1.0/t/pg/" rdf:about=""'
    f' xmp:ModifyDate="{_TYPED_CHANGED_AT}" xmpMM:DocumentID="xmp.did:3F2A9C"'
    ' pdf:Trapped="False" pdfaid:part="2" pdfaid:conformance="B">'
    "<xmp:CreateDate>2024-05-03T10:00:00Z</xmp:CreateDate>"
    f"<xmp:MetadataDate>{_TYPED_CHANGED_AT}</xmp:MetadataDate>"
    f"<pdf:Producer>{_TYPED_PRODUCER}</pdf:Producer>"
    "<xmpMM:InstanceID>uuid:5d3c0f12-2024-11e9</xmpMM:InstanceID>"
    "<dc:format>application/pdf</dc:format>"
    "<pdf:PDFVersion>1.7</pdf:PDFVersion>"
    "<xmpTPg:NPages>12</xmpTPg:NPages>"
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
    '<?xpacket end="w"?>'
)
_TYPED_LINE = "Begin to end: ID did 2024 05 application PDF False 12"


@pytest.fixture(scope="module")
def typed_metadata() -> bytes:
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 100), _TYPED_LINE, fontname="helv", fontsize=12)
    pdf.set_xml_metadata(_TYPED_XMP)
    return pdf.tobytes()


def test_a_redaction_leaves_the_metadatas_wrapper_and_typed_values_as_they_were(
    mine, typed_metadata
):
    doc = upload(mine, typed_metadata).json()
    line = span_starting(doc, 0, "Begin")

    rendered = render(mine, doc, [_redact(line)], [around(line)], scale=1).json()
    response = _export(mine, doc, [_redact(line)])

    places = rendered["redactions"][line["id"]]["hidden_copies"]
    assert_equal(places, [], "the places render names")
    saved = _opened(response)
    assert_equal(saved.get_xml_metadata(), _with_mark(_TYPED_XMP, saved), "the metadata")
    assert_equal(_info(saved, "Producer"), "squidpdf", "the program the Info names")


def _with_mark(xmp: str, pdf: pymupdf.Document) -> str:
    changed_at = _changed_at(pdf).strftime("%Y-%m-%dT%H:%M:%SZ")
    return xmp.replace(_TYPED_PRODUCER, "squidpdf").replace(_TYPED_CHANGED_AT, changed_at)


def test_an_exported_files_xmp_says_what_its_info_says(mine, typed_metadata):
    doc = upload(mine, typed_metadata).json()

    saved = _opened(_export(mine, doc, []))

    xmp = saved.get_xml_metadata()
    changed_at = _changed_at(saved).strftime("%Y-%m-%dT%H:%M:%SZ")
    said = {
        "ModifyDate": re.findall(r'xmp:ModifyDate="([^"]*)"', xmp),
        "MetadataDate": re.findall(r"<xmp:MetadataDate>([^<]*)<", xmp),
        "Producer": re.findall(r"<pdf:Producer>([^<]*)<", xmp),
        "CreateDate": re.findall(r"<xmp:CreateDate>([^<]*)<", xmp),
    }
    expected = {
        "ModifyDate": [changed_at],
        "MetadataDate": [changed_at],
        "Producer": ["squidpdf"],
        "CreateDate": ["2024-05-03T10:00:00Z"],
    }
    assert_equal(said, expected, "what the XMP says of the file's last change and its making")


def _page_xmp_of(pdf: pymupdf.Document, page: pymupdf.Page) -> str:
    _value_type, stream = pdf.xref_get_key(page.xref, "Metadata")
    return pdf.xref_stream(int(stream.split()[0])).decode()


def _annotation_xmp_of(pdf: pymupdf.Document, page: pymupdf.Page) -> str:
    _value_type, comments = pdf.xref_get_key(page.xref, "Annots")
    [stream] = re.findall(r"/Metadata (\d+) 0 R", comments)
    return pdf.xref_stream(int(stream)).decode()


def _kept_in(pdf: pymupdf.Document) -> dict[str, object]:
    page = pdf[0]
    catalog = pdf.pdf_catalog()
    note, free_text, stale_free_text, stamp = page.annots()
    fields = {field.field_name: field for field in page.widgets()}
    field, choice = fields["who"], fields["pick"]
    undrawable, listed = fields["sealed"].xref, fields["team"].xref
    return {
        "title": pdf.xref_get_key(_TRAILER, "Info/Title"),
        "author": pdf.xref_get_key(_TRAILER, "Info/Author"),
        "xmp": re.findall(r">([^<>]+)<", pdf.get_xml_metadata()),
        "page_xmp": re.findall(r">([^<>]+)<", _page_xmp_of(pdf, page)),
        "annotation_xmp": re.findall(r">([^<>]+)<", _annotation_xmp_of(pdf, page)),
        "bookmark": tuple(
            pdf.xref_get_key(catalog, f"Outlines/{end}/Title") for end in ("First", "Last")
        ),
        "note": (
            note.info["content"],
            note.info["title"],
            note.info["subject"],
            pdf.xref_get_key(note.xref, "RC"),
        ),
        "free_text": free_text.info["content"],
        "stale_free_text": (
            stale_free_text.info["content"],
            stale_free_text.get_text().strip(),
        ),
        "field": (
            field.field_value,
            pdf.xref_get_key(field.xref, "DV"),
            pdf.xref_get_key(field.xref, "RV"),
        ),
        "undrawable_field": (
            pdf.xref_get_key(undrawable, "V"),
            pdf.xref_get_key(undrawable, "AP"),
        ),
        "choice": (choice.field_value, pdf.xref_get_key(choice.xref, "Opt")),
        "multi_choice": tuple(pdf.xref_get_key(listed, key) for key in ("V", "Opt", "I")),
        "unlisted_field": fields["signed"].field_value,
        "tooltip": pdf.xref_get_key(fields["described"].xref, "TU"),
        "widget_contents": pdf.xref_get_key(fields["phone"].xref, "Contents"),
        "seed_value": pdf.xref_get_key(fields["to_sign"].xref, "SV"),
        "button": pdf.xref_get_key(fields["mail"].xref, "MK"),
        "stamp": pdf.xref_get_key(stamp.xref, "AP"),
        "tag": pdf.xref_get_key(catalog, "StructTreeRoot/K/Alt"),
    }


def test_a_document_deleted_while_its_export_runs_still_downloads(app, mine, doc, monkeypatch):
    monkeypatch.setattr(app.state, "pool", InProcess())

    def deleted_once_open(folder: Path) -> Engine:
        engine = store.FOLDER_STORE.open_original(folder)
        store.FOLDER_STORE.delete(folder)
        return engine

    monkeypatch.setattr(app.state, "documents", StoreWith(opens_original=deleted_once_open))
    span = span_starting(doc, 1, "Invoices")
    ninety = span["text"].replace("thirty", "ninety")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": ninety}])

    assert_in("ninety days", _opened(response)[1].get_text(), "the downloaded page")
    assert_false((store.root() / doc["id"]).exists(), "the document's folder is still there")


def test_an_export_killed_at_its_timeout_leaves_nothing_in_the_temp_folder(
    app, mine, doc, monkeypatch, own_pool, tmp_path
):
    monkeypatch.setenv(
        "TMPDIR", str(tmp_path)
    )  # the workers' temp folder, read when they start
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # and the server's
    monkeypatch.setattr(app.state, "pool", own_pool)
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_stalling_driver))
    monkeypatch.setattr(editing_constants, "EXPORT_TIMEOUT_S", _STALLED_TIMEOUT_S)
    span = span_starting(doc, 1, "Invoices")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": "x"}])

    assert_problem(response, "too_slow", 503)
    assert_equal(sorted(tmp_path.iterdir()), [], "left in the temp folder")


def test_pages_come_out_in_the_order_asked_with_redactions_read_where_they_went(mine, three):
    response = _export(mine, three, [_redact(span_starting(three, 0, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], ["First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def _rotate(page: int, degrees: int) -> dict:
    return {"kind": "rotate_page", "page": page, "degrees": degrees}


def _delete(page: int) -> dict:
    return {"kind": "delete_page", "page": page}


def _move(page: int, before: int | None) -> dict:
    return {"kind": "move_page", "page": page, "before": before}


@pytest.mark.parametrize(
    ("ops", "lines", "turns"),
    [
        ([_rotate(1, 90)], ["First page", "Second page", "Third page"], [0, 90, 0]),
        (
            [_rotate(1, 270), _rotate(1, 180)],
            ["First page", "Second page", "Third page"],
            [0, 90, 0],
        ),
        ([_delete(1)], ["First page", "Third page"], [0, 0]),
        ([_move(2, 0)], ["Third page", "First page", "Second page"], [0, 0, 0]),
        ([_move(0, None)], ["Second page", "Third page", "First page"], [0, 0, 0]),
    ],
    ids=["turned", "turned twice, past a whole turn", "left out", "moved before", "moved last"],
)
def test_each_page_op_comes_out_in_the_file(mine, three, ops, lines, turns):
    pdf = _opened(_export(mine, three, ops))

    got = [_lines(pdf)[page][-1] for page in range(pdf.page_count)]
    assert_equal(got, lines, "each page's own line, in order")
    assert_equal([page.rotation for page in pdf.pages()], turns, "each page's turn")


def test_page_ops_fold_in_list_order_and_the_last_page_stays(mine, three):
    ops = [_delete(0), _rotate(0, 90), _delete(1), _delete(2)]

    response = _export(mine, three, ops)

    assert_equal(response.headers[_SKIPPED], "3", "the edits left out")
    assert_equal(_lines(_opened(response)), [[_HEADER, "Third page"]], "the page that stays")
    skipped = render(mine, three, ops, [], scale=1).json()["skipped"]
    said = [(s["edit"], s["code"], s["detail"]) for s in skipped]
    assert_equal(said, [(3, "last_page", words.sentence("last_page"))], "why it was left out")


def test_pages_are_the_final_order_and_may_not_name_a_page_an_edit_deleted(mine, three):
    moved = _export(mine, three, [_move(0, None)], pages=[0, 2])
    assert_equal(
        [lines[-1] for lines in _lines(_opened(moved))],
        ["First page", "Third page"],
        "pages asked, in the order asked",
    )

    assert_problem(_export(mine, three, [_delete(1)], pages=[0, 1]), "invalid_request", 400)


def test_a_redaction_is_read_where_the_page_ops_put_its_page(mine, three):
    ops = [
        _redact(span_starting(three, 0, _HEADER)),
        _move(0, None),
        _rotate(0, 90),
        _delete(1),
    ]

    pdf = _opened(_export(mine, three, ops))

    assert_equal(_lines(pdf), [[_HEADER, "Third page"], ["First page"]], "each page's lines")
    assert_equal([page.rotation for page in pdf.pages()], [0, 90], "each page's turn")


def test_a_redaction_on_a_page_left_out_does_not_fail_the_export(mine, three):
    response = _export(mine, three, [_redact(span_starting(three, 1, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], [_HEADER, "First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


@pytest.fixture(scope="module")
def filled_in() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Name:", fontname="helv", fontsize=12)
    page.add_freetext_annot(pymupdf.Rect(120, 88, 260, 104), "Jane Doe", fontsize=12)
    return doc.tobytes()


@pytest.mark.parametrize(
    ("edit", "lines"),
    [
        ({"kind": "redact"}, ["Name:"]),
        ({"kind": "replace", "text": "John Roe"}, ["Name:", "John Roe"]),
    ],
    ids=["a redacted answer goes", "a replaced one reads as typed"],
)
def test_an_answer_filled_in_with_a_comment_edits_like_the_page_text(
    mine, filled_in, edit, lines
):
    doc = upload(mine, filled_in).json()
    answer = span_starting(doc, 0, "Jane Doe")

    response = _export(mine, doc, [{**edit, "span_id": answer["id"]}])

    assert_equal(_lines(_opened(response)), [lines], "the page's lines")


def test_a_split_of_a_tagged_file_says_the_tags_went_with_the_pages_left_out(mine, tagged):
    doc = upload(mine, Path(tagged).read_bytes()).json()

    response = _export(mine, doc, [], pages=[0])

    said = json.loads(response.headers[_NOTICES])
    expected = [("file", "tags_dropped", words.sentence("tags_dropped"))]
    got = [(n["kind"], n["code"], n["detail"]) for n in said]
    assert_equal(got, expected, "what came out other than asked")
    saved = _opened(response)
    assert_equal(
        [page.get_text().strip() for page in saved.pages()], TAGGED_LINES[:1], "pages' text"
    )
    # Kept by its tags, the page left out would still be in the file, unseen but readable.
    assert_equal(_page_objects(saved), 1, "page objects in the file")


@pytest.fixture(scope="module")
def described(three_pages) -> bytes:
    doc = pymupdf.open(stream=three_pages, filetype="pdf")
    doc.set_metadata(
        {
            "title": "Lease",
            "creator": "Writer",
            "producer": "Printer",
            "modDate": "D:20240503100000Z",
        }
    )
    return doc.tobytes()


@pytest.mark.parametrize(
    ("edited", "pages"),
    [(True, None), (False, None), (False, [2, 0])],
    ids=["an edit", "no edit", "a split's part"],
)
def test_an_exported_file_says_squidpdf_changed_it_and_when(mine, described, edited, pages):
    doc = upload(mine, described).json()
    first = span_starting(doc, 0, "First")
    edits = [{"kind": "replace", "span_id": first["id"], "text": "One"}] if edited else []
    asked_at = datetime.now(UTC).replace(microsecond=0)  # a PDF date keeps whole seconds

    response = _export(mine, doc, edits, pages=pages)

    saved = _opened(response)
    said = [_info(saved, key) for key in ("Producer", "Title", "Creator")]
    assert_equal(said, ["squidpdf", "Lease", "Writer"], "what the Info holds")
    changed_at = _changed_at(saved).timestamp()
    assert_at_least(changed_at, asked_at.timestamp(), "when the Info says it changed")
    assert_at_most(changed_at, time.time(), "when the Info says it changed")


def _odd_info(shape: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "First page", fontname="helv", fontsize=12)
    if shape == "in the trailer":
        doc.xref_set_key(_TRAILER, "Info", "<</Title(Lease)/Producer(Printer)>>")
    if shape == "leading nowhere":
        doc.xref_set_key(_TRAILER, "Info", "99 0 R")
    if shape == "not a dictionary":
        text = doc.get_new_xref()
        doc.update_object(text, "(just words)")
        doc.xref_set_key(_TRAILER, "Info", f"{text} 0 R")
    return doc.tobytes()


@pytest.mark.parametrize(
    ("shape", "title"),
    [("in the trailer", "Lease"), ("leading nowhere", "null"), ("not a dictionary", "null")],
)
def test_an_info_kept_oddly_still_exports_and_names_squidpdf(mine, shape, title):
    doc = upload(mine, _odd_info(shape)).json()

    saved = _opened(_export(mine, doc, []))

    said = [_info(saved, key) for key in ("Producer", "Title")]
    assert_equal(said, ["squidpdf", title], "what the Info holds")


_OWNER_PASSWORD = "owner"
_AES_256 = 5  # pymupdf.PDF_ENCRYPT_AES_256, which its type stubs leave out
_MAY_PRINT_AND_READ_ALOUD = 4 | 512
_AS_OWNER = 4
_PROTECTION_KEYS = ("Filter", "V", "R", "Length", "P", "EncryptMetadata")


def _owner_only(doc: pymupdf.Document) -> bytes:
    """Some permissions are off, so a download that came out with every one on would show."""
    allowed = _MAY_PRINT_AND_READ_ALOUD
    return doc.tobytes(encryption=_AES_256, owner_pw=_OWNER_PASSWORD, permissions=allowed)


@pytest.fixture(scope="module")
def owner_only(three_pages) -> bytes:
    return _owner_only(pymupdf.open(stream=three_pages, filetype="pdf"))


def _protection_of(pdf_bytes: bytes) -> tuple[list[str], bool]:
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    encrypt_entries = [_protection_entry(pdf, key) for key in _PROTECTION_KEYS]
    return encrypt_entries, pdf.authenticate(_OWNER_PASSWORD) == _AS_OWNER


def _protection_entry(pdf: pymupdf.Document, key: str) -> str:
    value_type, value = pdf.xref_get_key(_TRAILER, f"Encrypt/{key}")
    if value_type != "xref":
        return value
    number = int(value.split()[0])
    # Naming no object: a reader takes it as null.
    if number >= pdf.xref_length():
        return "null"
    return pdf.xref_object(number, compressed=True)


@pytest.mark.parametrize("pages", [None, [1, 2]], ids=["every page", "a split's part"])
def test_a_file_with_only_an_owner_password_downloads_protected_as_it_came(
    mine, owner_only, pages
):
    doc = upload(mine, owner_only).json()
    second = span_starting(doc, 1, "Second")
    edits = [{"kind": "replace", "span_id": second["id"], "text": "Page two"}]

    response = _export(mine, doc, edits, pages=pages)

    lines = [line for page in _lines(_opened(response)) for line in page]
    assert_in("Page two", lines, "the download's lines")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(owner_only), "how the download is protected")


def test_a_redaction_in_a_file_with_only_an_owner_password_is_checked_and_kept_protected(
    mine, owner_only
):
    doc = upload(mine, owner_only).json()

    response = _export(mine, doc, [_redact(span_starting(doc, 0, "First"))])

    assert_not_in("First page", _lines(_opened(response))[0], "the first page's lines")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(owner_only), "how the download is protected")


_AES_128 = 4  # pymupdf.PDF_ENCRYPT_AES_128: its key, as RC4's, is made from the file's ID
_FILE_ID = re.compile(rb"/ID\s*\[[^\]]*\]")


@pytest.fixture(scope="module")
def owner_only_without_an_id(three_pages) -> bytes:
    """As some programs write one: its key made from an empty first ID, then the ID left out."""
    doc = pymupdf.open(stream=three_pages, filetype="pdf")
    doc.xref_set_key(_TRAILER, "ID", "[<> <>]")  # MuPDF keeps the empty first part
    protected = doc.tobytes(
        encryption=_AES_128,
        owner_pw=_OWNER_PASSWORD,
        permissions=_MAY_PRINT_AND_READ_ALOUD,
        use_objstms=False,
    )
    return _FILE_ID.sub(b"", protected, count=1)


def test_a_file_with_only_an_owner_password_and_no_id_downloads_protected_as_it_came(
    mine, owner_only_without_an_id
):
    doc = upload(mine, owner_only_without_an_id).json()
    first = span_starting(doc, 0, "First")
    edits = [{"kind": "replace", "span_id": first["id"], "text": "Page one"}]

    response = _export(mine, doc, edits)

    assert_in("Page one", _lines(_opened(response))[0], "the first page's lines")
    said = _protection_of(response.content)
    assert_equal(
        said, _protection_of(owner_only_without_an_id), "how the download is protected"
    )


@pytest.fixture(scope="module")
def owner_only_with_a_script(three_pages) -> bytes:
    """The upload drops the script, so its stored original is the file written again."""
    doc = pymupdf.open(stream=three_pages, filetype="pdf")
    script = doc.get_new_xref()
    doc.update_object(script, "<</S/JavaScript/JS(app.alert('opened'))>>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{script} 0 R")
    return _owner_only(doc)


def test_a_file_with_only_an_owner_password_keeps_its_protection_when_its_upload_drops_a_script(
    mine, owner_only_with_a_script
):
    doc = upload(mine, owner_only_with_a_script).json()

    response = _export(mine, doc, [])

    notices = [notice["type"] for notice in doc["notices"]]
    assert_in("active_content_dropped", notices, "what the upload said")
    said = _protection_of(response.content)
    expected = _protection_of(owner_only_with_a_script)
    assert_equal(said, expected, "how the download is protected")


_FIXTURES = Path(__file__).parents[2] / "fixtures"
_PLAIN_XMP = [
    "plain_xmp_aes128.pdf",
    "plain_xmp_aes256.pdf",
    "plain_xmp_shared.pdf",
    "plain_xmp_pieceinfo.pdf",
]
_PLAIN_XMP_TITLE = "Quarterly report"


def _xmp_objects_in(pdf: pymupdf.Document) -> list[int]:
    return [
        xref
        for xref in range(1, pdf.xref_length())
        if pdf.xref_get_key(xref, "Type") == ("name", "/Metadata")
    ]


@pytest.mark.parametrize("name", _PLAIN_XMP)
def test_a_protected_files_plain_xmp_is_left_out_of_the_download_and_said(mine, name):
    sent = (_FIXTURES / name).read_bytes()
    doc = upload(mine, sent).json()

    response = _export(mine, doc, [])

    notices = [notice["type"] for notice in doc["notices"]]
    assert_in("plain_xmp_dropped", notices, "what the upload said")
    saved = _opened(response)
    assert_equal(_xmp_objects_in(saved), [], "the XMP left in the file")
    assert_equal(_info(saved, "Title"), _PLAIN_XMP_TITLE, "the Info's title")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


_ENTRY_VALUE = r"<<(?:[^<>]|<<[^<>]*>>)*>>|<[0-9A-Fa-f]*>|\([^)]*\)|/[^/<>()\[\] ]+| [^/<>]+"


def _with_update(sent: bytes, objects: dict[int, str]) -> bytes:
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    written = [f"{number} 0 obj\n{source}\nendobj\n" for number, source in objects.items()]
    offsets = list(accumulate(map(len, written[:-1]), initial=len(sent)))
    table = "".join(
        f"{number} 1\n{at:010d} 00000 n \n" for number, at in zip(objects, offsets, strict=True)
    )
    size = max(pdf.xref_length(), max(objects) + 1)
    trailer = re.sub(r"/Size \d+", f"/Size {size}", pdf.pdf_trailer(compressed=True))
    entries = trailer.removesuffix(">>")
    previous = re.findall(rb"startxref\s+(\d+)", sent)[-1].decode()
    table_at = offsets[-1] + len(written[-1])
    update = (
        f"{''.join(written)}xref\n{table}trailer\n{entries}/Prev {previous}>>\n"
        f"startxref\n{table_at}\n%%EOF\n"
    )
    return sent + update.encode()


def _encrypt_object(pdf: pymupdf.Document) -> int:
    return int(pdf.xref_get_key(_TRAILER, "Encrypt")[1].split()[0])


def _with_entry_apart(sent: bytes, entry: str, *, references: int) -> bytes:
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = _encrypt_object(pdf)
    apart = pdf.xref_length()
    source = pdf.xref_object(encrypt, compressed=True)
    value = re.findall(rf"/{entry}({_ENTRY_VALUE})", source)[0]
    source = source.replace(f"/{entry}{value}", f"/{entry} {apart} 0 R", 1)
    last = apart + references - 1
    naming = {number: f"{number + 1} 0 R" for number in range(apart, last)}
    return _with_update(sent, {encrypt: source, **naming, last: value})


def _entries_apart(pdf: pymupdf.Document) -> list[str]:
    encrypt = _encrypt_object(pdf)
    keys = pdf.xref_get_keys(encrypt)
    return [key for key in keys if pdf.xref_get_key(encrypt, key)[0] == "xref"]


@pytest.mark.parametrize(
    ("name", "entry", "references"),
    [
        (_PLAIN_XMP[0], "V", 1),
        (_PLAIN_XMP[0], "EncryptMetadata", 1),
        (_PLAIN_XMP[1], "EncryptMetadata", 1),
        *((_PLAIN_XMP[0], entry, 1) for entry in ("O", "U", "Filter", "CF")),
        *((_PLAIN_XMP[1], entry, 1) for entry in ("OE", "Perms")),
        (_PLAIN_XMP[0], "EncryptMetadata", 2),
        (_PLAIN_XMP[0], "O", 2),
    ],
    ids=[
        "version",
        "flag, AES-128",
        "flag, AES-256",
        *("O U Filter CF".split()),
        *("OE Perms".split()),
        "flag, through two objects",
        "O, through two objects",
    ],
)
def test_a_protected_file_downloads_protected_as_it_came_with_an_entry_kept_apart(
    mine, name, entry, references
):
    sent = _with_entry_apart((_FIXTURES / name).read_bytes(), entry, references=references)
    uploaded = upload(mine, sent)

    assert_equal(uploaded.status_code, 201, uploaded.text[:200])
    doc = uploaded.json()
    response = _export(mine, doc, [])
    saved = _opened(response)
    assert_in(_PLAIN_XMP_TITLE, saved[0].get_text(), "the download's page")
    assert_equal(_entries_apart(saved), [], "the download's protection kept as objects")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")
    notices = [notice["type"] for notice in doc["notices"]]
    assert_in("plain_xmp_dropped", notices, "what the upload said")
    assert_equal(_xmp_objects_in(saved), [], "the XMP left in the file")


_STD_CF = "<</AuthEvent/DocOpen/CFM/AESV2/Length 16>>"
_CRYPT_FILTERS = f"/CF<</StdCF{_STD_CF}>>"


def _with_crypt_filter_apart(sent: bytes, *, crypt_filters: str, objects: list[str]) -> bytes:
    """The objects take the next free numbers, which the sources name as `{a}` and `{b}`."""
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = _encrypt_object(pdf)
    a = pdf.xref_length()
    source = pdf.xref_object(encrypt, compressed=True)
    source = source.replace(_CRYPT_FILTERS, crypt_filters.format(a=a, b=a + 1), 1)
    added = {a + n: kept.format(a=a, b=a + 1) for n, kept in enumerate(objects)}
    return _with_update(sent, {encrypt: source, **added})


@pytest.mark.parametrize(
    ("crypt_filters", "objects"),
    [
        ("/CF<</AAA {a} 0 R/StdCF {a} 0 R>>", [_STD_CF]),
        ("/AAA {a} 0 R/CF<</StdCF {a} 0 R>>", [_STD_CF]),
        ("/CF {a} 0 R", ["<</StdCF {b} 0 R>>", _STD_CF.replace(">>", "/Back {a} 0 R>>")]),
    ],
    ids=["twice in CF", "beside CF", "naming CF again"],
)
def test_a_crypt_filter_named_twice_is_still_written_in_place(mine, crypt_filters, objects):
    aes_128 = (_FIXTURES / _PLAIN_XMP[0]).read_bytes()
    sent = _with_crypt_filter_apart(aes_128, crypt_filters=crypt_filters, objects=objects)
    uploaded = upload(mine, sent)

    assert_equal(uploaded.status_code, 201, uploaded.text[:200])
    response = _export(mine, uploaded.json(), [])
    saved = _opened(response)
    assert_in(_PLAIN_XMP_TITLE, saved[0].get_text(), "the download's page")
    crypt_filter, _source = saved.xref_get_key(_TRAILER, "Encrypt/CF/StdCF")
    assert_equal(crypt_filter, "dict", "how the download keeps its crypt filter")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


def _with_objects_named_beside_protection(sent: bytes, *, links: str, depth: int) -> bytes:
    """`links` names the next object as `{next}`."""
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = _encrypt_object(pdf)
    first = pdf.xref_length()
    source = pdf.xref_object(encrypt, compressed=True).removesuffix(">>")
    run = {
        number: f"<<{links.format(next=number + 1)}>>" for number in range(first, first + depth)
    }
    return _with_update(
        sent, {encrypt: f"{source}/Extra {first} 0 R>>", **run, first + depth: "<<>>"}
    )


@pytest.mark.parametrize(
    ("links", "depth"),
    [("/A {next} 0 R/B {next} 0 R", 20), ("/Next {next} 0 R", 1200)],
    ids=["each naming the next twice", "a long run"],
)
def test_a_protection_naming_objects_no_reader_reads_opens_as_it_came(mine, links, depth):
    aes_128 = (_FIXTURES / _PLAIN_XMP[0]).read_bytes()
    sent = _with_objects_named_beside_protection(aes_128, links=links, depth=depth)
    uploaded = upload(mine, sent)

    assert_equal(uploaded.status_code, 201, uploaded.text[:200])
    response = _export(mine, uploaded.json(), [])
    saved = _opened(response)
    assert_in(_PLAIN_XMP_TITLE, saved[0].get_text(), "the download's page")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


_RC4_128 = 3


def _flag_written_as(pdf: pymupdf.Document, *, encryption: int, flag: bytes) -> bytes:
    """Saved without object streams, the trailer is last: a longer flag moves none."""
    protected = pdf.tobytes(encryption=encryption, owner_pw=_OWNER_PASSWORD, use_objstms=False)
    return protected.replace(b"/EncryptMetadata true", b"/EncryptMetadata " + flag, 1)


def _flag_naming_the_next_object(pdf: pymupdf.Document) -> bytes:
    """The file holds no such object, so a reader takes the flag as no flag."""
    protected = pdf.tobytes(encryption=_AES_128, owner_pw=_OWNER_PASSWORD, use_objstms=False)
    next_object = pymupdf.open(stream=protected, filetype="pdf").xref_length()
    return _flag_written_as(pdf, encryption=_AES_128, flag=f"{next_object} 0 R".encode())


@pytest.mark.parametrize(
    "protect",
    [
        lambda pdf: pdf.tobytes(encryption=_AES_128, owner_pw=_OWNER_PASSWORD),
        lambda pdf: pdf.tobytes(encryption=_AES_256, owner_pw=_OWNER_PASSWORD),
        # Below V4 the flag means nothing: the XMP is encrypted with the rest.
        partial(_flag_written_as, encryption=_RC4_128, flag=b"false"),
        # No true or false: a reader takes it as true, as with no flag.
        partial(_flag_written_as, encryption=_AES_128, flag=b"(false)"),
        _flag_naming_the_next_object,
    ],
    ids=["AES-128", "AES-256", "RC4 asking plain", "a string", "a reference to no object"],
)
def test_a_protected_files_encrypted_xmp_is_kept_and_nothing_is_said(
    mine, typed_metadata, protect
):
    sent = protect(pymupdf.open(stream=typed_metadata, filetype="pdf"))
    doc = upload(mine, sent).json()

    response = _export(mine, doc, [])

    notices = [notice["type"] for notice in doc["notices"]]
    assert_not_in("plain_xmp_dropped", notices, "what the upload said")
    saved = _opened(response)
    assert_equal(saved.get_xml_metadata(), _with_mark(_TYPED_XMP, saved), "the metadata")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


def test_an_edit_pointing_at_nothing_is_skipped_and_named_in_the_header(mine, doc):
    span = span_starting(doc, 1, "Invoices")
    ninety = span["text"].replace("thirty", "ninety")
    edits = [
        {"kind": "replace", "span_id": "nosuchspan00", "text": "x"},
        {"kind": "replace", "span_id": span["id"], "text": ninety},
        {"kind": "insert", "page": 7, "origin": [72, 700], "text": "Signed", "size": 12},
    ]

    response = _export(mine, doc, edits)

    assert_equal(response.headers[_SKIPPED], "0, 2", "edits left out")
    assert_in("ninety days", _opened(response)[1].get_text(), "the edit that was good")
    skipped = render(mine, doc, edits, []).json()["skipped"]
    said = [(s["edit"], s["type"], s["detail"]) for s in skipped]
    expected = [
        (0, "bad_reference", words.sentence("no_span")),
        (2, "bad_reference", words.sentence("no_page")),
    ]
    assert_equal(said, expected, "why each edit was left out")


@pytest.mark.parametrize(
    "asked", [{}, {"range": [0, 4]}], ids=["all of it", "some words of it"]
)
def test_an_edit_after_a_redaction_on_the_same_text_fails_the_export(mine, doc, asked):
    span = next(s for s in doc["spans"] if s["page"] == 1)
    redact = {"kind": "redact", "span_id": span["id"], **asked}
    replace = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [redact, replace])

    assert_problem(response, "redaction_conflict", 422)


# MuPDF's erase rewrites a quote so the line lands above where it was, its letters still drawn.
_QUOTED = "BT /F 12 Tf 14 TL 72 114 Td {spacing}(Acct 1234 Name) {quote} ET"


@pytest.mark.parametrize(
    "drawing",
    [
        _QUOTED.format(spacing="6 1 ", quote='"'),
        _QUOTED.format(spacing="", quote="'"),
        _QUOTED.format(spacing="", quote="'")
        + " BT /F 12 Tf 72.4 100 Td (Acct 1234 Name) Tj ET",
        "/Span <</ActualText (Card number)>> BDC "
        "BT /F 12 Tf 14 TL 72 114 Td (4421 8812) ' ET EMC",
    ],
    ids=["a double quote", "a single quote", "drawn twice over", "said in other words"],
)
def test_a_line_the_erase_moves_off_its_box_downloads_nothing(mine, drawing):
    doc = upload(mine, drawn_page(drawing)).json()
    span = doc["spans"][0]
    _assert_refused_as_still_drawn(
        mine, doc, span=span, edits=[_redact(span)], regions=[around(span)]
    )


def _dense_line(path: Path) -> bytes:
    write_dense(str(path), pages=1)
    return path.read_bytes()


@pytest.mark.parametrize(
    ("sent", "starts"),
    [
        (lambda _path: drawn_page(_THREE_LINES.format("Code A1", "150", "50.00 due")), "150"),
        (
            lambda _path: drawn_page(
                _THREE_LINES.format("Total amount", "Sent to Ann", "on file")
            ),
            "Sent",
        ),
        (_dense_line, "change this paid services"),
    ],
    ids=[
        "a number its neighbours spell",
        "words its neighbours spell",
        "the sample generator's",
    ],
)
def test_a_line_whose_neighbours_spell_its_words_once_it_goes_downloads(
    mine, tmp_path, sent, starts
):
    doc = upload(mine, sent(tmp_path / "dense.pdf")).json()
    span = next(each for each in doc["spans"] if each["text"].startswith(starts))

    rendered = render(mine, doc, [_redact(span)], [around(span)], scale=1).json()
    response = _export(mine, doc, [_redact(span)])

    assert_equal(rendered["redactions"][span["id"]]["verified"], True, "render's check")
    assert_not_in(span["text"], _lines(_opened(response))[0], "the page's lines, downloaded")


_THREE_LINES = (
    "BT /F 12 Tf 72 150 Td ({}) Tj ET BT /F 12 Tf 72 134 Td ({}) Tj ET "
    "BT /F 12 Tf 72 118 Td ({}) Tj ET"
)


def test_a_line_whose_letters_the_erase_misses_downloads_nothing(mine, tmp_path):
    path = named_with_widths(
        str(tmp_path / "arial.pdf"), "Liberation Sans Regular", base_font="Arial"
    )
    named = pymupdf.open(path)
    [(font_object, *_rest)] = named[0].get_fonts()
    named.xref_set_key(font_object, "FirstChar", "null")
    doc = upload(mine, named.tobytes()).json()
    [span] = doc["spans"]
    _assert_refused_as_still_drawn(
        mine, doc, span=span, edits=[_redact(span)], regions=[around(span)]
    )


# MuPDF's erase swaps lines drawn by a quote after a negative leading.
_SWAPPED = (
    "BT /F 12 Tf -20 TL 72 60 Td (Acct 1234 Name) ' ET "
    "BT /F 12 Tf -80 TL {x} 40 Td (4321 Acct Name) ' ET "
    "BT /F 12 Tf 300 120 Td (Zed) Tj ET"
)


@pytest.mark.parametrize(
    ("asked", "x"), [(range(5, 9), 98.672), (None, 72)], ids=["some words of it", "all of it"]
)
def test_lines_an_erase_swaps_download_nothing(mine, asked, x):
    doc = upload(mine, drawn_page(_SWAPPED.format(x=x))).json()
    account, zed = span_starting(doc, 0, "Acct 1234"), span_starting(doc, 0, "Zed")
    redact = _redact(account) if asked is None else _redact_words(account, asked)
    edits = [redact, _redact(zed)]
    _assert_refused_as_still_drawn(mine, doc, span=account, edits=edits, regions=[{"page": 0}])


@pytest.mark.parametrize(
    ("redacted", "other", "problem"),
    [
        ("Ann", "(Annual report) Tj", "redaction_moved"),
        ("1234", "(ref 12 34) Tj", "redaction_moved"),
        ("1234", "(ref 1234) Tj", "redaction_failed"),
        ("1234", "[(ref)-333(1234)] TJ", "redaction_failed"),
    ],
    ids=[
        "inside a longer word",
        "its letters apart",
        "the words themselves",
        "the words themselves, kerned apart",
    ],
)
def test_a_line_an_erase_moves_is_the_words_still_drawn_only_where_it_spells_them(
    app, mine, monkeypatch, redacted, other, problem
):
    drawing = f"BT /F 12 Tf 20 100 Td ({redacted}) Tj ET BT /F 12 Tf 20 60 Td {other} ET"
    script = (
        (f"20 100 Td ({redacted})".encode(), b"20 100 Td ()"),
        (f"20 60 Td {other}".encode(), f"30 60 Td {other}".encode()),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    span = span_starting(doc, 0, redacted)

    response = _export(mine, doc, [_redact(span)])

    assert_problem(response, problem, 422)


_LEVEL_AND_BELOW = (
    "BT /F 12 Tf 72 100 Td (Acct 1234) Tj ET BT /F 12 Tf 300 100 Td (Spring 2024) Tj ET "
    "BT /F 12 Tf 72 60 Td (Other words) Tj ET"
)
_PAGE_HEIGHT = 842


_STILL = (0.0, 0.0)


def _off_further_along(origin: Place) -> Place:
    return (0.0, 3.0) if origin[0] >= 300 else _STILL


def _along_below(origin: Place) -> Place:
    return (2.0, 0.0) if origin[1] > _PAGE_HEIGHT - 80 else _STILL


@pytest.mark.parametrize(
    "moves", [_off_further_along, _along_below], ids=["off its line", "another line"]
)
def test_other_text_an_erase_moves_refuses_the_download_saying_so(
    app, mine, monkeypatch, moves
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(MovingAfterAnErase(moves)))
    doc = upload(mine, drawn_page(_LEVEL_AND_BELOW)).json()
    account = span_starting(doc, 0, "Acct")

    rendered = render(mine, doc, [_redact(account)], [{"page": 0}], scale=1).json()
    response = _export(mine, doc, [_redact(account)])

    said = rendered["redactions"][account["id"]]
    facts = {"text": account["text"], "page": 1}
    moved = words.render(Message("moved_other_text", facts))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)
    assert_equal(response.json()["params"], facts, "the words and the page, unsaid")


_QUOTED_BELOW = (
    "BT /F 12 Tf 72 150 Td (Acct 1234 Name) Tj ET "
    "BT /F 12 Tf 14 TL 72 {below} Td (Other words here) ' ET"
)


@pytest.mark.parametrize(
    ("drawing", "asked"),
    [
        (_QUOTED_BELOW.format(below=30), None),
        (_QUOTED_BELOW.format(below=30), range(5, 9)),
        (_QUOTED_BELOW.format(below=74), range(5, 9)),
    ],
    ids=[
        "drawn by a quote",
        "drawn by a quote, some words",
        "drawn by a quote, landing beside them",
    ],
)
def test_a_line_the_erase_moves_beside_the_redaction_refuses_the_download_saying_so(
    mine, drawing, asked
):
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    redact = _redact(account) if asked is None else _redact_words(account, asked)

    rendered = render(mine, doc, [redact], [around(account)], scale=1).json()
    response = _export(mine, doc, [redact])

    said = rendered["redactions"][account["id"]]
    facts = {"text": account["text"] if asked is None else "1234", "page": 1}
    moved = words.render(Message("moved_other_text", facts))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)
    detail = words.render(Message("redaction_moved", facts))
    assert_equal(response.json()["detail"], detail, "what the user reads")


@dataclass(frozen=True, slots=True, eq=False)
class _RewritingDriver:
    driver: Any
    script: tuple[tuple[bytes, bytes], ...]

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        doc = self.driver.doc
        [contents] = doc[page].get_contents()
        drawing = doc.xref_stream(contents)
        for piece, becomes in self.script:
            drawing = drawing.replace(piece, becomes)
        doc.update_stream(contents, drawing)
        return ["" for _box in boxes]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


@dataclass(frozen=True, slots=True)
class _Rewriting:
    """A worker opens the file, so the factory is sent there: no closure."""

    script: tuple[tuple[bytes, bytes], ...]

    def __call__(self, path: str) -> PdfDriver:
        # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
        return cast(PdfDriver, _RewritingDriver(open_driver(path), self.script))


def test_words_an_erase_sets_along_their_line_level_with_others_download_nothing(
    app, mine, monkeypatch
):
    drawing = (
        "BT /F 12 Tf 20 100 Td (Acct ) Tj ET BT /F 12 Tf 46.676 100 Td (1234) Tj ET "
        "BT /F 12 Tf 73.364 100 Td ( Name) Tj ET "
        "BT /F 12 Tf 300 100 Td (Ref ) Tj ET BT /F 12 Tf 322.008 100 Td (1234) Tj ET"
    )
    script = (
        (b"46.676 100 Td (1234)", b"200 100 Td (1234)"),
        (b"322.008 100 Td (1234)", b"322.008 100 Td ()"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][account["id"]]["verified"]
    assert_equal((verified, response.status_code), (False, 422), "render's answer, export's")


# Helvetica at 12: "Acct " is 26.676 wide, each digit 6.672.
_ACCT_LINE = (
    "BT /F 12 Tf 20 100 Td (Acct ) Tj ET BT /F 12 Tf 46.676 100 Td (1234) Tj ET "
    "BT /F 12 Tf 73.364 100 Td ( Name) Tj ET "
)
_ERASED = (b"46.676 100 Td (1234)", b"46.676 100 Td ()")
_OTHER = "BT /F 12 Tf 20 60 Td (Other) Tj ET "
_ZED = "BT /F 12 Tf 200 100 Td (Zed) Tj ET "


@pytest.mark.parametrize(
    ("drawing", "script"),
    [
        (
            _ACCT_LINE + _OTHER + _ZED,
            (_ERASED, (b"73.364 100 Td", b"83.364 100 Td"), (b"200 100 Td", b"260 100 Td")),
        ),
        (
            _ACCT_LINE + _ZED + _OTHER,
            (_ERASED, (b"73.364 100 Td", b"83.364 100 Td"), (b"200 100 Td", b"260 100 Td")),
        ),
        (
            _ACCT_LINE + _OTHER + _ZED,
            (_ERASED, (b"73.364 100 Td", b"240 100 Td"), (b"200 100 Td", b"80 100 Td")),
        ),
        (
            _ACCT_LINE + _OTHER + "BT /F 12 Tf 200 100 Td (Ref 5678) Tj ET",
            (_ERASED, (b"73.364 100 Td", b"196.664 100 Td")),
        ),
    ],
    ids=["set along two ways", "two ways, drawn together", "swapped", "onto another column"],
)
def test_the_rest_of_a_line_set_along_it_but_not_as_one_refuses_the_download_as_moved(
    app, mine, monkeypatch, drawing, script
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


def test_the_spans_on_a_whole_redacted_line_swapped_refuse_the_download_as_moved(
    app, mine, monkeypatch
):
    drawing = (
        "BT /F 12 Tf 20 100 Td (1234) Tj ET " + _OTHER + "BT /F 12 Tf 100 100 Td (Name) Tj ET "
        "BT /F 12 Tf 20 40 Td (More) Tj ET BT /F 12 Tf 200 100 Td (Zed) Tj ET"
    )
    script = (
        (b"20 100 Td (1234)", b"20 100 Td ()"),
        (b"100 100 Td (Name)", b"220 100 Td (Name)"),
        (b"200 100 Td (Zed)", b"100 100 Td (Zed)"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")
    _assert_refused_as_moved(mine, doc, number, [_redact(number)], text="1234")


def _assert_refused_as_moved(mine, doc: dict, span: dict, edits: list[dict], *, text: str):
    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][span["id"]]
    moved = words.render(Message("moved_other_text", {"text": text, "page": 1}))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)


def _assert_refused_as_still_drawn(
    mine, doc: dict, *, span: dict, edits: list[dict], regions: list[dict]
) -> None:
    rendered = render(mine, doc, edits, regions, scale=1).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][span["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


def _assert_downloads_saying_moved(
    mine, doc: dict, *, span: dict, edits: list[dict], text: str
) -> None:
    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][span["id"]]
    moved = words.render(Message("rest_of_line_moved", {"text": text, "page": 1}))
    assert_equal((said["verified"], said["message"]), (True, moved), "what render says")
    assert_not_in(text, _opened(response)[0].get_text(), "the downloaded page's text")


@pytest.mark.parametrize(
    ("to", "said"),
    [(b"2046.688", "rest_of_line_removed"), (b"575", "rest_of_line_moved")],
    ids=["all of it off the page, removed", "some of it still on it, moved"],
)
def test_the_rest_of_a_line_set_off_the_page_is_said_removed_unless_some_still_shows(
    app, mine, monkeypatch, to, said
):
    drawing = "BT /F 12 Tf 20 100 Td (1234) Tj ET BT /F 12 Tf 46.688 100 Td ( Name) Tj ET"
    script = ((b"20 100 Td (1234)", b"20 100 Td ()"), (b"46.688 100 Td", to + b" 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = span_starting(doc, 0, "1234")
    edits = [_redact_words(line, range(0, 4))]

    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()
    response = _export(mine, doc, edits)

    verdict = rendered["redactions"][line["id"]]
    message = words.render(Message(said, {"text": "1234", "page": 1}))
    assert_equal((verdict["verified"], verdict["message"]), (True, message), "what render says")
    assert_not_in("1234", _opened(response)[0].get_text(), "the downloaded page's text")


@pytest.mark.parametrize(
    "drawing",
    [
        # With no leading, MuPDF's erase of some words drops the T* and sets "Zed" onto "Note".
        "BT /F 12 Tf 0 TL 72 100 Td [-10000 (Acct 1234 Name)] TJ T* (Zed) Tj ET "
        "BT /F 12 Tf 280.7 100 Td (Note) Tj ET",
    ],
    ids=["onto other text"],
)
def test_text_on_the_redacted_line_moved_but_not_as_its_rest_refuses_the_download_as_moved(
    mine, drawing
):
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    rendered = render(mine, doc, edits, [around(account)], scale=1).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][account["id"]]
    facts: dict[str, Param] = {"text": "1234", "page": 1}
    moved = words.render(Message("moved_other_text", facts))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)
    detail = words.render(Message("redaction_moved", facts))
    assert_equal(response.json()["detail"], detail, "what the user reads")


@pytest.mark.parametrize(
    ("drawing", "asked", "said", "problem"),
    [
        (
            "BT /F 12 Tf 14 TL 20 114 Td (Acct 1234 ) ' ET",
            [None],
            "not_redacted",
            "redaction_failed",
        ),
        (
            _QUOTED_BELOW.format(below=30),
            [range(0, 4), range(10, 14)],
            "moved_other_text",
            "redaction_moved",
        ),
    ],
    ids=["all of a line", "two runs"],
)
def test_render_and_export_quote_the_same_words(mine, drawing, asked, said, problem):
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [
        _redact(account) if each is None else _redact_words(account, each) for each in asked
    ]

    rendered = render(mine, doc, edits, [around(account)], scale=1).json()
    response = _export(mine, doc, edits)

    quoted = "Acct 1234" if asked == [None] else "Acct … Name"
    facts: dict[str, Param] = {"text": quoted, "page": 1}
    message = rendered["redactions"][account["id"]]["message"]
    assert_equal(message, words.render(Message(said, facts)), "what render says")
    assert_problem(response, problem, 422)
    assert_equal(response.json()["params"]["text"], quoted, "the words export names")


def test_words_an_erase_sets_elsewhere_as_another_line_goes_download_nothing(
    app, mine, monkeypatch
):
    drawing = (
        "BT /F 12 Tf 20 100 Td (1234) Tj ET "
        "BT /F 12 Tf 20 60 Td (Page 1 of 2, item 3 of 4) Tj ET"
    )
    script = (
        (b"20 100 Td (1234)", b"200 140 Td (1234)"),
        (b"20 60 Td (Page 1 of 2, item 3 of 4)", b"20 60 Td ()"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")
    edits = [_redact(number)]
    _assert_refused_as_still_drawn(mine, doc, span=number, edits=edits, regions=[{"page": 0}])


@pytest.mark.parametrize(
    ("drawing", "script", "asked"),
    [
        (
            _ACCT_LINE + "BT /F 12 Tf 20 60 Td (Page 1 of 2, item 3 of 4) Tj ET",
            (
                (b"46.676 100 Td (1234)", b"200 140 Td (1234)"),
                (b"20 60 Td (Page 1 of 2, item 3 of 4)", b"20 60 Td ()"),
            ),
            range(5, 9),
        ),
        (
            "BT /F 12 Tf 20 100 Td (Card ) Tj ET BT /F 12 Tf 49.34 100 Td (1234 5678) Tj ET "
            "BT /F 12 Tf 106.052 100 Td ( ref ) Tj ET BT /F 12 Tf 126.728 100 Td (1234) Tj ET",
            (
                (b"126.728 100 Td (1234)", b"126.728 100 Td ()"),
                (b"49.34 100 Td (1234 5678)", b"186.728 100 Td (1234)"),
            ),
            range(5, 14),
        ),
    ],
    ids=["another line's letters gone", "one of two words, where the rest's went"],
)
def test_a_redacted_word_drawn_elsewhere_downloads_nothing_whatever_else_went(
    app, mine, monkeypatch, drawing, script, asked
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = doc["spans"][0]
    edits = [_redact_words(line, asked)]
    _assert_refused_as_still_drawn(mine, doc, span=line, edits=edits, regions=[{"page": 0}])


def test_the_same_words_set_along_the_line_are_the_words_still_drawn(mine):
    drawing = "BT /F 12 Tf 0 TL 20 100 Td [-10000 (Acct 1234 paid)] TJ T* (ref 1234) Tj ET"
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    response = _export(mine, doc, edits)

    assert_problem(response, "redaction_failed", 422)


# `_ACCT_LINE` again, 0.3 points along, as a bold is faked by drawing a line twice.
_ACCT_LINE_AGAIN = (
    "BT /F 12 Tf 20.3 100 Td (Acct ) Tj ET BT /F 12 Tf 46.976 100 Td (1234) Tj ET "
    "BT /F 12 Tf 73.664 100 Td ( Name) Tj ET "
)
_TIGHT_ABOVE = "BT /F 12 Tf 20 111 Td (gy oooooooooooo) Tj ET "
_ONTO = (_ERASED, (b"73.364 100 Td", b"140 100 Td"))


@pytest.mark.parametrize(
    "drawing",
    [
        _ACCT_LINE + "BT /F 12 Tf 150 101 Td (ZED) Tj ET",
        _ACCT_LINE + "BT /F 12 Tf 50 Tz 150 95 Td (ZED) Tj ET",
        _ACCT_LINE + "BT /F 12 Tf 1 0 0 3 150 80 Tm (ZED) Tj ET",
        _ACCT_LINE.replace("Name", "gypsy") + "BT /F 12 Tf 150 91 Td (ZED) Tj ET",
    ],
    ids=[
        "a point off its baseline",
        "condensed, 5 points below",
        "stretched up, 20 points below",
        "its descenders onto capitals",
    ],
)
def test_the_rest_of_a_line_set_onto_letters_as_drawn_refuses_as_moved(
    app, mine, monkeypatch, drawing
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(_ONTO)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


def test_the_rest_of_a_line_set_onto_a_letter_only_the_words_touched_refuses_as_moved(
    app, mine, monkeypatch
):
    drawing = (
        "BT /F 12 Tf 20 112 Td (pp) Tj ET BT /F 12 Tf 33.344 112 Td ( ay) Tj ET "
        r"BT /F 12 Tf 30 100 Td (\305) Tj ET"
    )
    script = ((b"20 112 Td (pp)", b"20 112 Td ()"), (b"33.344 112 Td", b"20 112 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = span_starting(doc, 0, "pp")
    _assert_refused_as_moved(mine, doc, line, [_redact_words(line, range(0, 2))], text="pp")


_RING_LINE = (
    r"BT /F 12 Tf 20 100 Td (\305cc ) Tj ET BT /F 12 Tf 43.34 100 Td (1234) Tj ET "
    r"BT /F 12 Tf 70.028 100 Td ( \305sy) Tj ET BT /F 12 Tf 20 112.5 Td (g) Tj ET "
)
_RING_MOVE = ((b"43.34 100 Td (1234)", b"43.34 100 Td ()"), (b"70.028 100 Td", b"43.34 100 Td"))


@pytest.mark.parametrize(
    "drawing",
    [
        _RING_LINE + "BT /F 12 Tf 46.376 112.5 Td (_) Tj ET",
        _RING_LINE.replace("20 112.5", "20 110") + "BT /F 12 Tf 47.676 110 Td (.) Tj ET",
        _RING_LINE.replace(r"(\305cc )", r"(\305yc )")
        + "BT /F 12 Tf 29 89 Td (l) Tj ET BT /F 12 Tf 61.18 89 Td (l) Tj ET",
    ],
    ids=["a whole underscore", "a whole full stop", "a letter below, as deep as another met"],
)
def test_the_rest_of_a_line_set_onto_a_letter_that_never_met_it_refuses_as_moved(
    app, mine, monkeypatch, drawing
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(_RING_MOVE)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = next(span for span in doc["spans"] if "1234" in span["text"])
    at = line["text"].index("1234")
    edits = [_redact_words(line, range(at, at + 4))]
    _assert_refused_as_moved(mine, doc, line, edits, text="1234")


@pytest.mark.parametrize(
    ("drawing", "script"),
    [
        (
            "BT /F 12 Tf 20 112.5 Td (oooogoooooo) Tj ET "
            "BT /F 12 Tf 20 100 Td (acc ) Tj ET BT /F 12 Tf 42.008 100 Td (1234) Tj ET "
            r"BT /F 12 Tf 68.696 100 Td ( \305sa) Tj ET ",
            (
                (b"42.008 100 Td (1234)", b"42.008 100 Td ()"),
                (b"68.696 100 Td", b"42.008 100 Td"),
            ),
        ),
        (
            "BT /F 60 Tf 84 114 Td (|) Tj ET BT /F 12 Tf 20 114 Td (oooooooooooooooo) Tj ET "
            "BT /F 12 Tf 20 100 Td (acc ) Tj ET BT /F 12 Tf 42.008 100 Td (1234) Tj ET "
            "BT /F 12 Tf 100 100 Td ( Name) Tj ET ",
            ((b"42.008 100 Td (1234)", b"42.008 100 Td ()"), (b"100 100 Td", b"68.696 100 Td")),
        ),
    ],
    ids=["a ring onto a descender", "onto a tall bar"],
)
def test_the_rest_of_a_line_set_onto_letters_its_line_never_touched_refuses_as_moved(
    app, mine, monkeypatch, drawing, script
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "acc")
    edits = [_redact_words(account, range(4, 8))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


@pytest.mark.parametrize(
    ("drawing", "script"),
    [
        (_ACCT_LINE + "BT /F 12 Tf 150 91 Td (ZED) Tj ET", _ONTO),
        (
            _TIGHT_ABOVE + _ACCT_LINE,
            (_ERASED, (b"73.364 100 Td", b"63.364 100 Td")),
        ),
        (
            "q BT /F 12 Tf 2000 Tz 20 110 Td (_) Tj ET Q " + _ACCT_LINE,
            (_ERASED, (b"73.364 100 Td", b"63.364 100 Td")),
        ),
        (
            _ACCT_LINE + _ACCT_LINE_AGAIN,
            (
                _ERASED,
                (b"46.976 100 Td (1234)", b"46.976 100 Td ()"),
                (b"73.364 100 Td", b"63.364 100 Td"),
                (b"73.664 100 Td", b"63.664 100 Td"),
            ),
        ),
    ],
    ids=[
        "clear of capitals below",
        "under a line set tighter than solid, its letters touching already",
        "under a rule each letter touched as much before",
        "drawn twice over, as for bold",
    ],
)
def test_the_rest_of_a_line_set_along_onto_nothing_else_downloads_saying_so(
    app, mine, monkeypatch, drawing, script
):
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_downloads_saying_moved(mine, doc, span=account, edits=edits, text="1234")


@dataclass(frozen=True, slots=True, eq=False)
class _UnboxedDriver:
    driver: Any
    fewer: bool

    def drawn_glyph_boxes(self, page: int) -> list[Rect] | None:
        return self.driver.drawn_glyph_boxes(page)[:-1] if self.fewer else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


@dataclass(frozen=True, slots=True)
class _Unboxed:
    script: tuple[tuple[bytes, bytes], ...]
    fewer: bool

    def __call__(self, path: str) -> PdfDriver:
        rewriting = _RewritingDriver(open_driver(path), self.script)
        # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
        return cast(PdfDriver, _UnboxedDriver(rewriting, self.fewer))


@pytest.mark.parametrize("fewer", [True, False], ids=["a box short", "none read"])
def test_a_rest_whose_letters_can_t_be_boxed_refuses_saying_it_couldn_t_be_checked(
    app, mine, monkeypatch, fewer
):
    script = (_ERASED, (b"73.364 100 Td", b"63.364 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Unboxed(script, fewer)))
    doc = upload(mine, drawn_page(_ACCT_LINE)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    rendered = render(mine, doc, edits, [{"page": 0}], scale=1).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][account["id"]]
    unchecked = words.render(Message("not_checked", {"text": "1234", "page": 1}))
    assert_equal((said["verified"], said["message"]), (False, unchecked), "what render says")
    assert_problem(response, "redaction_unchecked", 422)
    detail = words.render(Message("redaction_unchecked", {"text": "1234", "page": 1}))
    assert_equal(response.json()["detail"], detail, "what the user reads")


def test_a_span_of_the_line_set_into_a_gap_before_another_refuses_as_moved(
    app, mine, monkeypatch
):
    script = (
        (b"46.676 100 Td (1234)", b"46.676 100 Td ()"),
        (b"200 100 Td (Zed)", b"46.676 100 Td (Zed)"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(_ACCT_LINE + _OTHER + _ZED)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


def test_the_spans_after_a_whole_redaction_set_along_download_saying_so(app, mine, monkeypatch):
    drawing = (
        "BT /F 12 Tf 20 100 Td (Head |) Tj ET BT /F 12 Tf 100 100 Td (1234) Tj ET "
        + _OTHER
        + "BT /F 12 Tf 400 100 Td (2024) Tj ET"
    )
    script = ((b"100 100 Td (1234)", b"100 100 Td ()"), (b"400 100 Td", b"420 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")
    _assert_downloads_saying_moved(mine, doc, span=number, edits=[_redact(number)], text="1234")


def _on_zed_line(box: Rect) -> bool:
    return box.y1 < _PAGE_HEIGHT - 120


def test_render_checks_a_redaction_after_every_erase_on_its_page_as_export_does(
    app, mine, monkeypatch
):
    drawing = (
        "BT /F 12 Tf 72 100 Td (Acct 1234) Tj ET BT /F 12 Tf 72 140 Td (Zed) Tj ET "
        "BT /F 12 Tf 72 60 Td (Other words) Tj ET"
    )
    moving = MovingAfterAnErase(_along_below, when=_on_zed_line)
    monkeypatch.setattr(app.state, "documents", store.FolderStore(moving))
    doc = upload(mine, drawn_page(drawing)).json()
    account, zed = span_starting(doc, 0, "Acct"), span_starting(doc, 0, "Zed")
    edits = [_redact(account), _redact(zed)]

    rendered = render(mine, doc, edits, [around(account)], scale=1).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][account["id"]]["verified"]
    assert_equal((verified, response.status_code), (False, 422), "render's answer, export's")


def test_a_letter_whose_middle_sits_on_its_box_s_rounded_edge_goes_with_it(mine):
    drawing = "BT /F 12 Tf 20 100 Td (1234) Tj ET BT 1 0 0 rg /F 12 Tf 45.361 100 Td (i) Tj ET"
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")

    saved = _opened(_export(mine, doc, [_redact(number)]))

    assert_equal(saved[0].get_text().split(), [], "the letters left on the page")


def test_a_redaction_pointing_at_nothing_fails_the_export(mine, doc):
    response = _export(mine, doc, [{"kind": "redact", "span_id": "nosuchspan00"}])
    assert_problem(response, "bad_reference", 422)
    got = response.json()
    expected = ("bad_reference", {"span_id": "nosuchspan00"})
    assert_equal((got["code"], got["params"]), expected, "the problem, unsaid")


@pytest.mark.parametrize(
    "asked",
    [[19, 99], [9, 5], [5, 5], [4, 5], [-1, 3]],
    ids=["past its end", "backwards", "empty", "only a space", "before its start"],
)
def test_a_redaction_of_words_its_line_does_not_have_fails_the_export(mine, asked):
    doc = upload(mine, _with_a_card_line()).json()
    card = span_starting(doc, 0, "Card")

    response = _export(mine, doc, [{"kind": "redact", "span_id": card["id"], "range": asked}])

    assert_problem(response, "bad_range", 422)
    said = words.sentence("bad_range").format(span_id=card["id"])
    assert_equal(response.json()["detail"], said, "what the user reads")


def _redact_words(span: dict, asked: range) -> dict:
    return {"kind": "redact", "span_id": span["id"], "range": [asked.start, asked.stop]}


def _letters_drawn(pdf: pymupdf.Document) -> list[tuple[str, float, float]]:
    return [
        (char["c"], round(char["origin"][0], 2), round(char["origin"][1], 2))
        for char in each_letter(pdf[0].get_text("rawdict")["blocks"])
    ]


@pytest.mark.parametrize(
    ("ranges", "title"),
    [
        ([_FIRST_NUMBER], "Card then 9918 ends"),
        ([_FIRST_NUMBER, _SECOND_NUMBER], "Card then ends"),
        ([range(5, 14), range(10, 19)], "Card ends"),
    ],
    ids=["one number", "both numbers", "overlapping words"],
)
def test_words_of_a_line_redacted_go_and_the_rest_stays_where_it_was(mine, ranges, title):
    _assert_words_go_and_the_rest_stays(mine, ranges, title)


class _At:
    """Its save's date writes "4421", the card line's first number."""

    @staticmethod
    def now(tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 11, 2, 5, 44, 21, tzinfo=tz)


def test_a_save_whose_date_spells_a_word_redacted_is_still_without_it(app, mine, monkeypatch):
    monkeypatch.setattr(app.state, "pool", InProcess())
    monkeypatch.setattr(export, "datetime", _At)
    _assert_words_go_and_the_rest_stays(mine, [_FIRST_NUMBER, _SECOND_NUMBER], "Card then ends")


def _assert_words_go_and_the_rest_stays(mine, ranges: list[range], title: str) -> None:
    sent = _with_a_card_line()
    doc = upload(mine, sent).json()
    card = span_starting(doc, 0, "Card")
    before = _letters_drawn(pymupdf.open(stream=sent, filetype="pdf"))

    response = _export(mine, doc, [_redact_words(card, asked) for asked in ranges])

    saved = _opened(response)
    taken = {at for asked in ranges for at in asked}
    kept = [letter for at, letter in enumerate(before) if at not in taken]
    assert_equal(_letters_drawn(saved), kept, "the line's other letters, each where it was")
    assert_equal(_info(saved, "Title"), title, "the title, less the words redacted")
    everything = _everything_in(saved)
    for word in re.finditer(r"\S+", _TWO_NUMBERS_LINE):
        if taken.issuperset(range(word.start(), word.end())):
            assert_not_in(word[0], everything, "a word redacted, anywhere in the file")


def _glyphs_drawn(pdf: pymupdf.Document) -> str:
    letters = (chr(char[0]) for run in pdf[0].get_texttrace() for char in run["chars"])
    return "".join(letter for letter in letters if not letter.isspace())


_ACCOUNT = "BT /F 12 Tf 72 100 Td (Acct 1234 Name) Tj ET"


@pytest.mark.parametrize(
    ("sent", "starts", "size", "asked", "drawn", "title"),
    [
        (
            drawn_page(f"/Span <</ActualText (Name Acct 1234)>> BDC {_ACCOUNT} EMC"),
            "Name",
            None,
            range(10, 14),
            "AcctName",
            "null",
        ),
        (
            drawn_page(
                "BT /F 6 Tf 74 118 Td (Card 4421 then) Tj ET "
                "BT /F 30 Tf 72 110 Td (Card 4421 then) Tj ET"
            ),
            "Card 4421 then",
            30,
            range(5, 9),
            "Card4421thenCardthen",
            "null",
        ),
        (
            drawn_page("BT /F 12 Tf 72 100 Td (1234 and 1234 end) Tj ET"),
            "1234",
            None,
            range(9, 13),
            "1234andend",
            "null",
        ),
        (
            drawn_page(_ACCOUNT, title="Acct1234 Name"),
            "Acct",
            None,
            range(5, 9),
            "AcctName",
            "Acct Name",
        ),
        (
            drawn_page(_ACCOUNT, title="Acct1234Name and 1234Acct"),
            "Acct",
            None,
            range(5, 9),
            "AcctName",
            "AcctName and Acct",
        ),
        (
            drawn_page(
                "BT /F 12 Tf 72 100 Td (Order 20 total 12) Tj ET", title="Order 2012 summary"
            ),
            "Order",
            None,
            range(15, 17),
            "Order20total",
            "Order 2012 summary",
        ),
        (
            drawn_page("BT /F 12 Tf 72 100 Td (Ref 1 - 1 Month) Tj ET", title="Plan 11"),
            "Ref",
            None,
            range(8, 9),
            "Ref1-Month",
            "Plan",
        ),
        (
            drawn_page(
                "BT /F 12 Tf 72 100 Td (Acct 1234567 Name) Tj ET", title="Acct 1234567 Name"
            ),
            "Acct",
            None,
            range(8, 12),
            "AcctName",
            "Acct Name",
        ),
        (
            drawn_page(
                "BT /F 12 Tf 72 100 Td (Acct ) Tj "
                "/Span <</ActualText (9999)>> BDC (1234) Tj EMC ( Name) Tj ET",
                title="Acct 9999 Name",
            ),
            "Acct",
            None,
            range(5, 9),
            "",
            "Acct Name",
        ),
    ],
    ids=[
        "said in another order",
        "a small copy in its box",
        "the second of two the same",
        "joined in the title",
        "joined in the title on either side",
        "joined in the title to a word not beside it",
        "joined in the title to the same word beside it",
        "from inside a word",
        "said in other words",
    ],
)
def test_the_words_a_redaction_takes_are_its_own_whole_wherever_the_file_has_them(
    mine, sent, starts, size, asked, drawn, title
):
    doc = upload(mine, sent).json()
    span = next(
        each
        for each in doc["spans"]
        if each["text"].startswith(starts) and size in (None, each["size"])
    )

    saved = _opened(_export(mine, doc, [_redact_words(span, asked)]))

    assert_equal(_glyphs_drawn(saved), drawn, "the letters drawn, spaces aside")
    assert_equal(_info(saved, "Title"), title, "the title")


def test_a_link_over_the_line_goes_when_it_holds_the_words_redacted(mine):
    line = "BT /F 12 Tf 72 100 Td (Mail jane@acme.com now) Tj ET"
    doc = upload(mine, drawn_page(line, link=("mailto:jane@acme.com", "Mail"))).json()
    span = span_starting(doc, 0, "Mail")

    saved = _opened(_export(mine, doc, [_redact_words(span, range(5, 18))]))

    assert_equal(saved[0].get_links(), [], "the links left on the page")


@pytest.mark.parametrize(
    ("pages", "problem", "status"),
    [
        ([2], "no_such_page", 422),
        ([0, 0], "invalid_request", 400),
        ([], "invalid_request", 400),
    ],
    ids=["past the last page", "a page twice", "no page at all: leave pages out for every one"],
)
def test_pages_the_document_cannot_give_are_a_problem_not_a_crash(
    mine, doc, pages, problem, status
):
    assert_problem(_export(mine, doc, [], pages=pages), problem, status)


_LOWERED = 3


@pytest.mark.parametrize(
    ("limit", "made", "problem"),
    [
        ("MAX_EDITS", lambda span: [_redact(span)] * (_LOWERED + 1), "too_many_edits"),
        (
            "MAX_TEXT_CHARS",
            lambda span: [{"kind": "replace", "span_id": span["id"], "text": "x" * 4}],
            "text_too_long",
        ),
        (
            "MAX_TEXT_CHARS",
            lambda _span: [
                {"kind": "insert", "page": 0, "origin": [72, 700], "text": "xxxx", "size": 12}
            ],
            "text_too_long",
        ),
    ],
    ids=["too many edits", "a replacement too long", "an insert too long"],
)
def test_an_edit_list_past_a_limit_is_refused(mine, doc, monkeypatch, limit, made, problem):
    monkeypatch.setattr(editing_constants, limit, _LOWERED)
    edits = made(doc["spans"][0])
    assert_problem(_export(mine, doc, edits), problem, 422)


@pytest.fixture(scope="module")
def longest(tmp_path_factory) -> bytes:
    """Copied rather than drawn page by page, so it builds quickly."""
    one_page = tmp_path_factory.mktemp("longest") / "one.pdf"
    write_dense(str(one_page), pages=1)
    longest = pymupdf.open()
    with pymupdf.open(one_page) as one:
        for _ in range(documents_constants.MAX_PAGES):
            longest.insert_pdf(one)
    return longest.tobytes()


def test_the_longest_document_exports_within_the_export_timeout(mine, longest, monkeypatch):
    monkeypatch.setattr(documents_constants, "ANALYSE_TIMEOUT_S", _UNTIMED_S)
    doc = upload(mine, longest).json()
    last_page = documents_constants.MAX_PAGES - 1
    line = span_starting(doc, last_page, _OPENING)
    edit = {"kind": "replace", "span_id": line["id"], "text": f"{_OPENING} 15 March 2026"}

    # Within EXPORT_TIMEOUT_S, or the pool stops it and the reply is too_slow.
    exported = _opened(_export(mine, doc, [edit]))

    assert_equal(exported.page_count, documents_constants.MAX_PAGES, "pages exported")
    assert_in(edit["text"], exported[last_page].get_text(), "the edit, on the last page")


_READ_ALIKE = ("Liberation Serif Regular", "Liberation Sans Regular")
_SERIF_LINE = "Set in the serif"
_SANS_LINE = "Set in the sans"


def _sans_named_as_serif() -> bytes:
    serif = TTFont(io.BytesIO(face_bytes(FACES["Liberation Serif Regular"])))
    sans = TTFont(io.BytesIO(face_bytes(FACES["Liberation Sans Regular"])))
    for record in sans["name"].names:
        serifs = serif["name"].getName(
            record.nameID, record.platformID, record.platEncID, record.langID
        )
        if serifs is not None:
            record.string = serifs.toUnicode()
    renamed = io.BytesIO()
    sans.save(renamed)
    return renamed.getvalue()


@pytest.fixture(scope="module")
def read_alike() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="serif", fontbuffer=face_bytes(FACES["Liberation Serif Regular"]))
    page.insert_font(fontname="sans", fontbuffer=_sans_named_as_serif())
    page.insert_text((72, 96), _SERIF_LINE, fontname="serif", fontsize=12)
    page.insert_text((72, 140), _SANS_LINE, fontname="sans", fontsize=12)
    return doc.tobytes()


def test_new_text_is_drawn_in_the_font_its_object_names_where_two_read_alike(mine, read_alike):
    doc = upload(mine, read_alike).json()
    [sans_object] = [
        font_object
        for font_object, *_, resource, _ in _fonts_of(read_alike)
        if resource == "sans"
    ]
    serif_line, sans_line = (span_starting(doc, 0, line) for line in (_SERIF_LINE, _SANS_LINE))
    read_as = {line["font"] for line in (serif_line, sans_line)}
    assert_equal(read_as, {serif_line["font"]}, "the two lines' fonts, read by one name")
    insert = {
        "kind": "insert",
        "page": 0,
        "origin": [72, 300],
        "text": "Signed",
        "size": 12,
        "font": sans_line["font"],
        "font_object": sans_object,
    }

    pdf = _opened(_export(mine, doc, [insert]))

    [signed] = pdf[0].search_for("Signed")
    faces = {face: pymupdf.Font(fontbuffer=face_bytes(FACES[face])) for face in _READ_ALIKE}
    widths = {face: font.text_length("Signed", fontsize=12) for face, font in faces.items()}
    drawn_in = min(widths, key=lambda face: abs(widths[face] - signed.width))
    assert_equal(drawn_in, "Liberation Sans Regular", "the face as wide as the new text")


def _fonts_of(pdf_bytes: bytes) -> list[tuple]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return pdf[0].get_fonts()
