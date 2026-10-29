"""Send edits, get the file back: every edit applied, and each redaction checked on it."""

from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import time
import zlib
from collections.abc import Collection, Iterator
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
    CARD_LINE,
    FIRST_NUMBER,
    SECOND_NUMBER,
    InProcess,
    MovingAfterAnErase,
    Place,
    around,
    drawn_page,
    span_starting,
    upload,
    verdicts_of,
    with_a_card_line,
)
from tests.conftest import (
    CHOSEN,
    NAME_COPIES,
    NOT_XML_COPIES,
    OTHER_LINE,
    REDACTED_NAME,
    SIGNED,
    StoreWith,
    cannot_cut,
    named_with_widths,
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
_OPENING = "This agreement is made on"  # how the long fixture's first line starts
_UNTIMED_S = 600  # time enough for any analysis: an upload isn't what's timed here
_NUMBER = "4111 1111 1111 1111"  # the marked fixture's card number, redacted
# Its line, drawn with an fi ligature; its "US" is in the kept line's "en-US" too.
_CARD_LINE = f"A US card on \ufb01le: {_NUMBER}"
_CARD = f"A US card on file {_NUMBER}"  # its line as its hidden copies hold it: typed, no colon
_KEPT = "Keep this line"  # its other line
_SCAN = f"{_CARD.lower().replace(' ', '_')}.png"  # an image's file name, as Word puts in an Alt
_SQUARE = "10 10 20 20 re f"  # a filled square, drawn inside marked content to keep it
# The line's hidden copy on that square.
_AROUND_SQUARE = f"/Span <</ActualText ({_CARD})>> BDC {_SQUARE} EMC"
# The card line's font: Helvetica, its code 1 the fi ligature.
_LIGATURES = (
    "<</Type/Font/Subtype/Type1/BaseFont/Helvetica"
    "/Encoding<</Type/Encoding/BaseEncoding/WinAnsiEncoding/Differences[1/fi]>>>>"
)
# A one-dot image written into a drawing, in hex, its end of data (>) against its EI.
_HEX_IMAGE = "BI /W 1 /H 1 /BPC 8 /CS /G /F /AHx ID 00>EI"
# A 4 by 4 image: 16 raw bytes after ID and a carriage return and line feed. They hold " EI ("
# and end in an E an I follows: only their count, from past the line feed, says where they end.
_UNFILTERED_IMAGE = "BI /W 4 /H 4 /BPC 8 /CS /G ID\r\n\0 EI (" + "\0" * 9 + "EI ( EI"
# zlib's header: deflate, with no dictionary and the least compression.
_ZLIB_HEADER = b"\x78\x01"
# A form: a drawing of its own, 100 points square, that a page draws like an image.
_FORM = {"Type": "/XObject", "Subtype": "/Form", "BBox": "[0 0 100 100]"}
# A tiling pattern: painted with its own colours, tiled at even spacing, every 20 points.
_TILING = {
    "PatternType": "1",
    "PaintType": "1",
    "TilingType": "1",
    "BBox": "[0 0 20 20]",
    "XStep": "20",
    "YStep": "20",
    "Resources": "<<>>",
}
# A Type3 font: each letter a drawing of its own, 1000 units to the point, "x" its one.
_TYPE3 = (
    "<</Type/Font/Subtype/Type3/FontBBox[0 0 1000 1000]/FontMatrix[0.001 0 0 0.001 0 0]"
    "/Encoding<</Type/Encoding/Differences[120/x]>>/FirstChar 120/LastChar 120/Widths[1000]"
    "/CharProcs<</x {letter} 0 R>>>>"
)
# The places `_marked_pdf` keeps a hidden copy that the redaction can rewrite.
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
# A hidden copy as a file writes it: the key, then the text in brackets.
_HIDDEN_COPY = re.compile(r"/(ActualText|Alt|E)\s*\(([^)]*)\)")


_STALL_S = 60  # far past any export timeout here
_STALLED_TIMEOUT_S = 3  # long enough to start the export, short enough to wait for


def _stalling_driver(path: str) -> PdfDriver:
    """MuPDF's driver, after hanging as MuPDF can on a hostile file: the worker dies first."""
    time.sleep(_STALL_S)
    return open_driver(path)


@pytest.fixture
def own_pool(server: TestClient) -> Iterator[WorkerPool]:
    """Workers of the test's own, since it kills one, made in the app's loop; closed after."""
    if server.portal is None:
        pytest.fail("the app isn't started")
    pool = server.portal.call(start_pool)
    yield pool
    server.portal.call(pool.close)


@pytest.fixture(scope="module")
def three_pages() -> bytes:
    """Three pages under the same header, each with its own line below it.

    The header repeats, so a redaction read on the wrong page finds it and fails.
    """
    doc = pymupdf.open()
    for line in _LINES:
        page = doc.new_page()
        page.insert_text((72, 72), _HEADER, fontname="helv", fontsize=10)
        page.insert_text((72, 100), line, fontname="helv", fontsize=12)
    return doc.tobytes()


@pytest.fixture
def three(mine, three_pages) -> dict:
    """The three pages as uploaded by `mine`."""
    return upload(mine, three_pages).json()


def _redact(span: dict) -> dict:
    """An edit redacting all of `span`."""
    return {"kind": "redact", "span_id": span["id"]}


def _new_text_at(span: dict, text: str, origin: list[float]) -> dict:
    """New text on `span`'s page, at its size, starting at `origin`."""
    return {"kind": "insert", "page": span["page"], "origin": origin, "text": text, "size": 11}


def _over(span: dict, text: str) -> dict:
    """New text drawn over `span`'s box, a little in from its start, on its last row."""
    return _new_text_at(span, text, [span["bbox"]["x0"] + 40, span["bbox"]["y1"]])


def _export(
    client, doc: dict, edits: list[dict], pages: list[int] | None = None, language: str = "en"
):
    """Ask for the file with `edits` applied, as the browser does, reading `language`."""
    body = {"edits": edits} if pages is None else {"edits": edits, "pages": pages}
    headers = {"Accept-Language": language}
    return client.post(f"/api/documents/{doc['id']}/export", json=body, headers=headers)


def _opened(response) -> pymupdf.Document:
    """The downloaded file, opened; fails, showing the reply, if it isn't one."""
    kind = response.headers["content-type"]
    assert_equal((response.status_code, kind), (200, "application/pdf"), response.text[:200])
    return pymupdf.open(stream=response.content, filetype="pdf")


def _info(pdf: pymupdf.Document, key: str) -> str:
    """What the file's Info holds under `key`; "null" when it holds nothing there."""
    return pdf.xref_get_key(-1, f"Info/{key}")[1]  # -1: the file's trailer


def _changed_at(pdf: pymupdf.Document) -> datetime:
    """When the file's Info says it last changed (its ModDate), in UTC."""
    return datetime.strptime(_info(pdf, "ModDate"), "D:%Y%m%d%H%M%SZ").replace(tzinfo=UTC)


def _files(doc: dict) -> list[str]:
    """Everything in the document's folder, however deep."""
    folder = store.root() / doc["id"]
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def _lines(pdf: pymupdf.Document) -> list[list[str]]:
    """Each page's lines of text, in page order."""
    return [page.get_text().splitlines() for page in pdf.pages()]


def test_a_face_that_could_not_be_cut_down_is_said_in_a_header_and_the_file_still_comes(
    app, mine, doc, monkeypatch, pseudo
):
    """The file is only larger, but it's said, in the reader's words and in ASCII."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
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


@pytest.mark.parametrize("pages", [None, [1, 0]], ids=["every page", "pages moved"])
def test_a_redaction_the_check_cannot_confirm_downloads_nothing(
    app, mine, doc, monkeypatch, pages
):
    """Text still in the saved file means no file, and the user is told which span.

    The erase is what's broken here, not the check: the text really is still
    in the file, and the check reads it where its page went. New text over it
    sharing no word isn't blamed, and it's named before one new text covers.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "remove", lambda _engine, _spans, then_drawn: [])
    span = span_starting(doc, 1, "Invoices")
    covered = span_starting(doc, 1, "Delivery")  # listed first, under its own "March"
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


@pytest.fixture(scope="module")
def marked() -> bytes:
    """A tagged page that keeps its card number beside the letters, in each place a page can."""
    return _marked_pdf(_PLACES)


def _marked_pdf(places: Collection[str]) -> bytes:
    """A tagged page that keeps its card line beside the letters, in each of `places`.

    Most wrap a square: MuPDF's erase drops marked content it leaves empty, and its Properties.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="helv")
    _kind, resources = doc.xref_get_key(page.xref, "Resources")  # "5 0 R": an object of its own
    resources_xref = int(resources.split()[0])
    doc.xref_set_key(resources_xref, "Font/Lig", _LIGATURES)
    number = f"({_NUMBER}) Tj"
    # The kept line, whose ActualText stays, and its language, which is no hidden copy.
    kept = f"<</Lang (en-US) /ActualText ({_KEPT})>>"
    drawing = [f"/Span {kept} BDC BT /helv 12 Tf 72 700 Td ({_KEPT}) Tj ET EMC"]
    # The number, in an ActualText MuPDF reads as its letters; and an E holding the line.
    if "drawing" in places:
        number = f"/Span <</ActualText ({_NUMBER})>> BDC {number} EMC"
        drawing.append(f"/Span <</E ({_CARD})>> BDC {_SQUARE} EMC")
    # A marked point (DP): marked content with nothing inside.
    if "point" in places:
        drawing.append(f"/Span <</ActualText ({_CARD})>> DP")
    # The page's Properties, named /MC0, rather than inline.
    if "properties" in places:
        doc.xref_set_key(resources_xref, "Properties/MC0", f"<</ActualText ({_CARD})>>")
        drawing.append(f"/Span /MC0 BDC {_SQUARE} EMC")
    # A form with an image's Alt naming its file after the line; its own words stay, "A" too.
    if "form" in places:
        alt = f"/Figure <</Alt (A scan: {_SCAN})>> BDC {_SQUARE} EMC"
        figure = _new_stream(doc, alt, **_FORM)
        doc.xref_set_key(resources_xref, "XObject/Fm0", f"{figure} 0 R")
        drawing.append("/Fm0 Do")
    # A tiling pattern, the line after a dictionary MuPDF can't read but draws on past.
    if "pattern" in places:
        unreadable = f"/Span <</ActualText (Logo) /Bad>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, f"{unreadable} {_AROUND_SQUARE}", **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P0", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P0 scn {_SQUARE}")
    # A soft mask the page paints a square through; its group is a drawing too.
    if "smask" in places:
        group = _new_stream(doc, _AROUND_SQUARE, **_FORM, Group="<</S/Transparency>>")
        mask = f"<</SMask<</S/Luminosity/G {group} 0 R>>>>"
        doc.xref_set_key(resources_xref, "ExtGState/GS0", mask)
        drawing.append(f"q /GS0 gs {_SQUARE} Q")
    # A Type3 letter "x" the page writes, its image first, as a bitmap font's letters are.
    if "type3" in places:
        letter = _new_stream(doc, f"1000 0 d0 {_HEX_IMAGE} {_AROUND_SQUARE}")
        font = doc.get_new_xref()
        doc.update_object(font, _TYPE3.format(letter=letter))
        doc.xref_set_key(resources_xref, "Font/T3", f"{font} 0 R")
        drawing.append("BT /T3 12 Tf 72 500 Td (x) Tj ET")
    # A square annotation's appearance, the drawing it shows, with an image first.
    if "annotation" in places:
        annotation = page.add_rect_annot(pymupdf.Rect(300, 300, 400, 330))
        _kind, appearance = doc.xref_get_key(annotation.xref, "AP/N")
        appearance_drawing = f"{_UNFILTERED_IMAGE} {_AROUND_SQUARE}"
        doc.update_stream(int(appearance.split()[0]), appearance_drawing.encode())
    # A tiling pattern whose hidden copy has a NUL before the line, where MuPDF stops reading.
    if "nul" in places:
        after_nul = f"/Span <</ActualText (Ref\\000{_CARD})>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, after_nul, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P3", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P3 scn {_SQUARE}")
    # The same as a string of its own (an object of the file), which the properties /MC1 name.
    if "nul_reference" in places:
        string = doc.get_new_xref()
        doc.update_object(string, f"(Ref\\000{_CARD})")
        doc.xref_set_key(resources_xref, "Properties/MC1", f"<</ActualText {string} 0 R>>")
        drawing.append(f"/Span /MC1 BDC {_SQUARE} EMC")
    # A tiling pattern with the dictionary before its tag, which MuPDF reads as if after.
    if "dictionary_first" in places:
        first = f"<</ActualText ({_CARD})>> /Span BDC {_SQUARE} EMC"
        tile = _new_stream(doc, first, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P5", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P5 scn {_SQUARE}")
    # A tiling pattern with ActualText written twice, the line first: MuPDF keeps "Logo".
    if "twice" in places:
        twice = f"/Span <</ActualText ({_CARD}) /ActualText (Logo)>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, twice, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P6", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P6 scn {_SQUARE}")
    # The same, each written in hex, so no ( says a string is there.
    if "twice_hex" in places:
        line, logo = (f"<{text.encode().hex()}>" for text in (_CARD, "Logo"))
        twice = f"/Span <</ActualText {line} /ActualText {logo}>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, twice, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P22", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P22 scn {_SQUARE}")
    # Alone: a dictionary MuPDF can't read, the line in UTF-16 hex: a check must read its text.
    if "unreadable" in places:
        line = f"<FEFF{_CARD.encode('utf-16-be').hex()}>"  # UTF-16, as hex
        unreadable = f"/Span <</ActualText {line} /Bad>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P1", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P1 scn {_SQUARE}")
    # Alone: the same, but MuPDF stops reading at a key that's a string, before the line.
    if "unreadable_first" in places:
        unreadable = f"/Span << (Logo) /ActualText ({_CARD}) >> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P2", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P2 scn {_SQUARE}")
    # Alone: the same, but MuPDF's parser stops quietly before the line, at an ID.
    if "unreadable_id" in places:
        unreadable = f"/Span <</ActualText (Logo) ID /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P7", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P7 scn {_SQUARE}")
    # Alone: the same, but a >> in an array before the line, which a count takes as the end.
    if "unreadable_array" in places:
        unreadable = f"/Span <</A [ >> ] /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P8", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P8 scn {_SQUARE}")
    # Alone: the same, then a key that's a string: both readings stop before the line.
    if "unreadable_both" in places:
        unreadable = f"/Span <</A [ >> ] (junk) /ActualText ({_CARD})>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P9", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P9 scn {_SQUARE}")
    # Alone: an image with no EI after its byte, then the line, which MuPDF never reaches.
    if "unreadable_image" in places:
        no_end = f"BI /W 1 /H 1 /BPC 8 /CS /G ID \0 {_AROUND_SQUARE}"
        tile = _new_stream(doc, no_end, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P4", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P4 scn {_SQUARE}")
    # Alone: the same, but 100 bytes, the first a %, which MuPDF's reader of a drawing takes as
    # a comment over the line, though a reader ending the image at the first EI reads the line.
    if "unreadable_image_comment" in places:
        no_end = f"BI /W 100 /H 1 /BPC 8 /CS /G ID %EI {_AROUND_SQUARE}{' ' * 100}"
        tile = _new_stream(doc, no_end, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P10", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P10 scn {_SQUARE}")
    # Alone: the same, its first byte a (: MuPDF's reader of a drawing reads one string from it,
    # the line in UTF-16 octal inside it, its mark of UTF-16 not first.
    if "unreadable_image_string" in places:
        in_utf16 = f"\ufeff{_CARD}".encode("utf-16-be")  # its mark first, as Word has it
        line = "".join(f"\\{byte:03o}" for byte in in_utf16)
        hidden_copy = f"/Span <</ActualText ({line})>> BDC {_SQUARE} EMC"
        no_end = f"BI /W 100 /H 1 /BPC 8 /CS /G ID (EI {hidden_copy}{' ' * 100}"
        tile = _new_stream(doc, no_end, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P11", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P11 scn {_SQUARE}")
    # Alone: an image where a value goes, its byte a <, then the line as UTF-16 hex: MuPDF's
    # reader of a drawing reads one hex string from that < past the line's, digits out of step.
    if "unreadable_image_hex" in places:
        image = "BI /W 1 /H 1 /BPC 8 /CS /G ID <EI"
        line = f"<FEFF{_CARD.encode('utf-16-be').hex()}>"
        unreadable = f"/Span <</X {image} /ActualText {line}>> BDC {_SQUARE} EMC"
        tile = _new_stream(doc, unreadable, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P12", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P12 scn {_SQUARE}")
    # Alone: an image MuPDF reads, its 200 bytes counted, holding the line after an EI: a
    # reader ending the image at its first EI reads the line as marked content.
    if "image_bytes" in places:
        image = f"BI /W 200 /H 1 /BPC 8 /CS /G ID {f'EI {_AROUND_SQUARE}':<200} EI"
        tile = _new_stream(doc, image, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P13", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P13 scn {_SQUARE}")
    # Alone: an image with no EI after its bytes, the first a ( and then a mark of UTF-16, so
    # its string reads only as UTF-16; a reader ending the image at the EI reads the line plain.
    if "plain_in_utf16" in places:
        in_string = f"(\\376\\377 EI {_AROUND_SQUARE})"
        no_end = f"BI /W 100 /H 1 /BPC 8 /CS /G ID {in_string}{' ' * 100}"
        tile = _new_stream(doc, no_end, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P14", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P14 scn {_SQUARE}")
    # Alone: a one-byte image, then an EI and a [, where MuPDF looks on for an EI after a space
    # but a reader taking the first EI past its bytes ends it, and reads the line.
    if "image_end_bracket" in places:
        image = f"BI /W 1 /H 1 /BPC 8 /CS /G ID \0 EI[ ] pop {_AROUND_SQUARE} EI"
        tile = _new_stream(doc, image, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P15", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P15 scn {_SQUARE}")
    # Alone: a one-dot image compressed (Flate), its dot in a block of its own and the line
    # after an EI in the next: MuPDF reads every block, a reader stopping at the dot reads on.
    if "image_flate_tail" in places:
        tail = f" EI[ ] pop {_AROUND_SQUARE} ".encode()
        compressed = b"BI /W 1 /H 1 /BPC 8 /CS /G /F /Fl ID " + _stored_flate(b"\0", tail)
        tile = _new_stream(doc, compressed + b" EI", **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P17", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P17 scn {_SQUARE}")
    # Alone: the same, its filter under both keys: /Filter, Flate, which MuPDF and Poppler read,
    # and /F, ASCII85, whose count of the same bytes would reach past the line.
    if "image_both_keys" in places:
        tail = f" EI[ ] pop {_AROUND_SQUARE} ".encode()
        dots = _stored_flate(b"A" * 100, tail)
        compressed = b"BI /W 100 /H 1 /BPC 8 /CS /G /F /A85 /Filter /Fl ID " + dots
        tile = _new_stream(doc, compressed + b" EI", **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P20", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P20 scn {_SQUARE}")
    # Alone: four dots in hex, but for letters that aren't hex digits, which a reader takes as
    # 0s: it has its dots before "EI[", where a count of hex digits alone runs past the line.
    if "image_hex_letters" in places:
        image = (
            f"BI /W 4 /H 1 /BPC 8 /CS /G /F /AHx ID 00gggggg00EI[ ] pop {_AROUND_SQUARE} > EI"
        )
        tile = _new_stream(doc, image, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P21", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P21 scn {_SQUARE}")
    # Alone: two dots after a carriage return and line feed, the second an E: MuPDF skips both,
    # so its dots end before "I", but a reader skipping one ends them before "EI[".
    if "image_crlf" in places:
        image = f"BI /W 2 /H 1 /BPC 8 /CS /G ID\r\n\0EI[ ] pop {_AROUND_SQUARE} EI"
        tile = _new_stream(doc, image, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P18", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P18 scn {_SQUARE}")
    # Alone: a tagged figure, its image's 100 bytes holding an EI with bytes no drawing has
    # after it, as compressed bytes may: no reader ends it there, so the line is only bytes.
    if "image_holding_end" in places:
        held = f"EI \xff ({_CARD}) \xff"
        image = f"BI /W 100 /H 1 /BPC 8 /CS /G ID {held:<100} EI"
        tagged = f"/Figure <</Alt (A figure)>> BDC {image} EMC"
        tile = _new_stream(doc, tagged, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P16", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P16 scn {_SQUARE}")
    # Alone: the same, its pixels written as ASCII85, all plain, its text an EI and the line: a
    # reader ends it at its ~>, or once it has its pixels, so the line is only pixels.
    if "a85_holding_end" in places:
        text = f"EI\n({_CARD})"
        pixels = base64.a85decode(text.encode(), ignorechars=b" \n")
        image = f"BI /W {len(pixels)} /H 1 /BPC 8 /CS /G /F /A85 ID {text}~> EI"
        tagged = f"/Figure <</Alt (A figure)>> BDC {image} EMC"
        tile = _new_stream(doc, tagged, **_TILING)
        doc.xref_set_key(resources_xref, "Pattern/P19", f"{tile} 0 R")
        drawing.append(f"/Pattern cs /P19 scn {_SQUARE}")
    drawing.append(f"BT /Lig 12 Tf 72 600 Td (A US card on \\001le: ) Tj {number} ET")
    contents = _new_stream(doc, "\n".join(drawing))
    doc.xref_set_key(page.xref, "Contents", f"{contents} 0 R")
    return doc.tobytes()


def _new_stream(doc: pymupdf.Document, drawing: str | bytes, **keys: str) -> int:
    """A new stream in `doc` holding `drawing`, with these keys; its number."""
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, drawing if isinstance(drawing, bytes) else drawing.encode())
    for key, value in keys.items():
        doc.xref_set_key(xref, key, value)
    return xref


def _stored_flate(*blocks: bytes) -> bytes:
    """A zlib stream of `blocks`, each stored as it is: a decoder gives each once it's read."""
    stored = [_stored_block(block, last=False) for block in blocks]
    checksum = zlib.adler32(b"".join(blocks)).to_bytes(4, "big")
    return _ZLIB_HEADER + b"".join(stored) + _stored_block(b"", last=True) + checksum


def _stored_block(block: bytes, *, last: bool) -> bytes:
    """A deflate block holding `block` as it is: whether it's last, its size, then inverted."""
    size = len(block)
    return (
        bytes([last])
        + size.to_bytes(2, "little")
        + (size ^ 0xFFFF).to_bytes(2, "little")
        + block
    )


# What a save writes of its own: the file's id, random, and its dates, the clock's. Either can
# spell any digits, and neither is the document's words.
_SAVES_OWN = re.compile(r"/ID\s*\[[^\]]*\]|/(?:CreationDate|ModDate)\s*\([^)]*\)")


def _everything_in(pdf: pymupdf.Document) -> str:
    """Every object in the file and every stream, decoded, as one text, but the save's own."""
    return _SAVES_OWN.sub(
        "",
        " ".join(
            f"{pdf.xref_object(xref)} {pdf.xref_stream(xref) or b''!r}"
            for xref in range(1, pdf.xref_length())
        ),
    )


def _hidden_copies_in(pdf: pymupdf.Document) -> list[tuple[str, str]]:
    """Each ActualText, Alt and E written in the file, as (key, text), wherever it is."""
    # A set: an object packed in an object stream is read twice, alone and in the stream.
    return sorted(set(_HIDDEN_COPY.findall(_everything_in(pdf))))


def test_a_redaction_leaves_no_hidden_copy_of_its_words_on_the_page(mine, marked):
    """A redaction removes its whole words from every hidden copy on its page, and no more."""
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
    """A hidden copy left in any one place fails the check: no file, the span named."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "drop_hidden_copies", lambda _engine, _spans: None)
    doc = upload(mine, _marked_pdf([place])).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=_CARD_LINE, page=1)
    assert_equal(response.json()["detail"], said, "what the user reads")


@pytest.mark.parametrize("place", ["image_holding_end", "a85_holding_end"])
def test_an_image_whose_bytes_only_hold_an_ei_still_downloads(mine, place):
    """Compressed bytes can hold an EI by chance; read as drawing, a short word would match.

    Here the line itself sits past it, where no reader reads the bytes as drawing.
    """
    doc = upload(mine, _marked_pdf([place])).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    assert_equal(_lines(_opened(response)), [[_KEPT]], "the page's lines")


@pytest.mark.parametrize("new_text", [[], ["card"]], ids=["alone", "under new text"])
def test_a_hidden_copy_the_redaction_cannot_rewrite_is_warned_at_render_and_refused_at_export(
    mine, new_text
):
    """The user hears it while they can still undo it, and the words never leave in a file.

    A dictionary MuPDF can't read holds the line in what a screen reader reads, so the
    redaction can't rewrite it. New text over it writes no hidden copy, so isn't blamed.
    """
    doc = upload(mine, _marked_pdf(["unreadable"])).json()
    card = span_starting(doc, 0, "A US card")
    edits = [_redact(card), *(_over(card, text) for text in new_text)]
    body = {"edits": edits, "scale": 1, "regions": [around(card)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
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
        # Drawn upside down, hanging from its origin: in the box, though a level line isn't.
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
    """The check reads the new text's "on" in the box and refuses: the user hears why.

    The redaction did its job; moving the new text off it is what lets the file go.
    """
    made = span_starting(doc, 0, "Made on")
    edits = [*turns, _redact(made), _new_text_at(made, "on", origin_of(made["bbox"]))]
    body = {"edits": edits, "scale": 1, "regions": [{"page": 0}]}

    exported = _export(mine, doc, edits)
    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()

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
    """Only the redacted words are looked for, so other words over the box pass the check."""
    made = span_starting(doc, 0, "Made on")
    response = _export(mine, doc, [_redact(made), _over(made, "Hello there")])

    first_page = _lines(_opened(response))[0]
    assert_equal(first_page, ["SERVICES AGREEMENT", "Hello there"], "the first page's lines")


def test_a_redaction_leaves_no_copy_of_its_words_anywhere_the_file_keeps_one(mine):
    """Each hidden copy loses the redacted words and keeps its own, or goes whole."""
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
    # MuPDF draws the stamp, its drawing gone, and the unsigned signature field its own way.
    expected_lines = [[OTHER_LINE, "Ask", "Ring", "APPROVED", CHOSEN, CHOSEN, "SIGN", "Email"]]
    assert_equal(
        _lines(saved), expected_lines, "the page's lines, the comments' and the fields'"
    )


@pytest.mark.parametrize("copy", [*NAME_COPIES, *NOT_XML_COPIES, "stamp_in_image"])
def test_each_hidden_copy_a_redaction_leaves_in_the_file_downloads_nothing(
    app, mine, monkeypatch, copy
):
    """A title or a bookmark is as much a leak as the page: no file, the span named.

    One copy at a time, so the check is shown to read each on its own.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "drop_hidden_copies", lambda _engine, _spans: None)
    doc = upload(mine, with_hidden_copies([copy])).json()
    name = span_starting(doc, 0, REDACTED_NAME)
    body = {"edits": [_redact(name)], "scale": 1, "regions": [around(name)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(name)])

    verified = rendered["redactions"][name["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=REDACTED_NAME, page=1)
    assert_equal(response.json()["detail"], said, "what the user reads")


@pytest.mark.parametrize("copy", NOT_XML_COPIES)
def test_metadata_that_is_not_xml_goes_whole_once_it_holds_a_redacted_word(mine, copy):
    """It can't be written back without the word as it was, so none of it stays."""
    doc = upload(mine, with_hidden_copies([copy])).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    response = _export(mine, doc, [_redact(name)])

    saved = _opened(response)
    metadata = saved.xref_get_key(saved.pdf_catalog(), "Metadata")
    assert_equal(metadata, ("null", "null"), "the metadata stream the catalog names")
    assert_not_in("Quill", _everything_in(saved), "the name, anywhere in the file")


@pytest.mark.parametrize("signed", SIGNED)
def test_a_redaction_in_a_signed_document_takes_every_signature_away(mine, signed):
    """The redaction rewrites the file, so no signature in it would still hold: each goes.

    None here holds the redacted name. Perms and DSS, there only for them, go too.
    """
    doc = upload(mine, with_hidden_copies([signed])).json()
    name = span_starting(doc, 0, REDACTED_NAME)
    body = {"edits": [_redact(name)], "scale": 1, "regions": [around(name)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(name)])

    said = words.sentence("signatures_removed")
    told = {"verified": True, "hidden_copies": ["signatures"], "message": said}
    assert_equal(verdicts_of(rendered), {name["id"]: told}, "what render says of it")
    expected = (["null"], "null", "null")
    assert_equal(_signatures_in(_opened(response)), expected, "the signatures, Perms and DSS")


def test_a_signed_document_exported_with_no_redaction_keeps_its_signatures(mine):
    """Only a redaction takes them away: a replace, or no edit, leaves each as export writes it.

    Broken, since export rewrites the file, so a reader says it changed after it was signed.
    """
    doc = upload(mine, with_hidden_copies(SIGNED)).json()
    line = span_starting(doc, 0, OTHER_LINE)
    edits = [{"kind": "replace", "span_id": line["id"], "text": "Other lane"}]

    edited = _export(mine, doc, edits)
    downloaded = _export(mine, doc, [])

    expected = (["xref"] * 3, "dict", "dict")
    assert_equal(_signatures_in(_opened(edited)), expected, "the edited file's signatures")
    assert_equal(_signatures_in(_opened(downloaded)), expected, "the downloaded file's")


def _signatures_in(pdf: pymupdf.Document) -> tuple[list[str], str, str]:
    """What kind each signature field's value is, then the catalog's Perms and DSS.

    A signature reads "xref"; a key the file leaves out, "null".
    """
    catalog = pdf.pdf_catalog()
    values = [pdf.xref_get_key(field.xref, "V")[0] for field in pdf[0].widgets()]
    return values, pdf.xref_get_key(catalog, "Perms")[0], pdf.xref_get_key(catalog, "DSS")[0]


_TYPED_CHANGED_AT = "2024-05-04T09:00:00Z"  # when its XMP says it last changed, both ways
_TYPED_PRODUCER = "Acrobat Distiller"  # the program its XMP names, with no word of its line
# Metadata (XMP) as Acrobat writes it: in its packet's wrapper, with typed values of each kind,
# its last change as an attribute and as an element; namespaces first, as the mark writes them.
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
# A line holding each word the packet's wrapper and the typed values are written with.
_TYPED_LINE = "Begin to end: ID did 2024 05 application PDF False 12"


@pytest.fixture(scope="module")
def typed_metadata() -> bytes:
    """A page of `_TYPED_LINE`, its metadata `_TYPED_XMP`."""
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 100), _TYPED_LINE, fontname="helv", fontsize=12)
    pdf.set_xml_metadata(_TYPED_XMP)
    return pdf.tobytes()


def test_a_redaction_leaves_the_metadatas_wrapper_and_typed_values_as_they_were(
    mine, typed_metadata
):
    """They say when, which or what kind, not what, so none is a copy.

    Rewritten, they wouldn't be valid, and the packet a PDF/A file must have would break.
    Only the mark sets its producer and the dates of its last change, as on every export.
    """
    doc = upload(mine, typed_metadata).json()
    line = span_starting(doc, 0, "Begin")
    body = {"edits": [_redact(line)], "scale": 1, "regions": [around(line)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(line)])

    places = rendered["redactions"][line["id"]]["hidden_copies"]
    assert_equal(places, [], "the places render names")
    saved = _opened(response)
    assert_equal(saved.get_xml_metadata(), _with_mark(_TYPED_XMP, saved), "the metadata")
    # The check reads the mark as a hidden copy, and "PDF" is no whole word of it.
    assert_equal(_info(saved, "Producer"), "squidpdf", "the program the Info names")


def _with_mark(xmp: str, pdf: pymupdf.Document) -> str:
    """`_TYPED_XMP`'s packet, naming squidpdf and the time `pdf`'s Info says it changed."""
    changed_at = _changed_at(pdf).strftime("%Y-%m-%dT%H:%M:%SZ")
    return xmp.replace(_TYPED_PRODUCER, "squidpdf").replace(_TYPED_CHANGED_AT, changed_at)


def test_an_exported_files_xmp_says_what_its_info_says(mine, typed_metadata):
    """A PDF/A file's XMP must agree with its Info: each names squidpdf and the same time."""
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
    """The page's own metadata (XMP), as text."""
    _value_type, stream = pdf.xref_get_key(page.xref, "Metadata")
    return pdf.xref_stream(int(stream.split()[0])).decode()


def _annotation_xmp_of(pdf: pymupdf.Document, page: pymupdf.Page) -> str:
    """The metadata (XMP) of the comment written in the page's list itself, as text."""
    _value_type, comments = pdf.xref_get_key(page.xref, "Annots")
    [stream] = re.findall(r"/Metadata (\d+) 0 R", comments)
    return pdf.xref_stream(int(stream)).decode()


def _kept_in(pdf: pymupdf.Document) -> dict[str, object]:
    """What each copy `with_hidden_copies` made of the name holds, read back from the file.

    A key the file leaves out reads ("null", "null").
    """
    page = pdf[0]
    catalog = pdf.pdf_catalog()
    note, free_text, stale_free_text, stamp = page.annots()
    fields = {field.field_name: field for field in page.widgets()}
    field, choice = fields["who"], fields["pick"]
    undrawable, listed = fields["sealed"].xref, fields["team"].xref
    return {
        "title": pdf.xref_get_key(-1, "Info/Title"),  # -1: the file's trailer
        "author": pdf.xref_get_key(-1, "Info/Author"),
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
    """Its owner's DELETE, or the sweep, takes the folder once the original is open.

    The export used to save into that folder, so the save failed and the
    browser was told the file needs more memory.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it

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
    """The edited file is the user's, and nothing of a document is kept past the hour.

    A killed worker runs no cleanup of its own, so the server makes and removes
    the folder the export saves into.
    """
    monkeypatch.setenv(
        "TMPDIR", str(tmp_path)
    )  # the workers' temp folder, read when they start
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # and the server's
    monkeypatch.setattr(app.state, "pool", own_pool)
    # A store whose driver hangs as it opens the original, in the worker.
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_stalling_driver))
    monkeypatch.setattr(export, "EXPORT_TIMEOUT_S", _STALLED_TIMEOUT_S)
    span = span_starting(doc, 1, "Invoices")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": "x"}])

    assert_problem(response, "too_slow", 503)
    assert_equal(sorted(tmp_path.iterdir()), [], "left in the temp folder")


def test_pages_come_out_in_the_order_asked_with_redactions_read_where_they_went(mine, three):
    """Page 0's header is redacted and page 0 comes out second: it's read, and gone, there."""
    response = _export(mine, three, [_redact(span_starting(three, 0, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], ["First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def _rotate(page: int, degrees: int) -> dict:
    """An edit turning `page` clockwise by `degrees`."""
    return {"kind": "rotate_page", "page": page, "degrees": degrees}


def _delete(page: int) -> dict:
    """An edit leaving `page` out."""
    return {"kind": "delete_page", "page": page}


def _move(page: int, before: int | None) -> dict:
    """An edit putting `page` before `before`, or last."""
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
    """Each page's own line, in the order the ops leave, turned as they turn it."""
    pdf = _opened(_export(mine, three, ops))

    got = [_lines(pdf)[page][-1] for page in range(pdf.page_count)]
    assert_equal(got, lines, "each page's own line, in order")
    assert_equal([page.rotation for page in pdf.pages()], turns, "each page's turn")


def test_page_ops_fold_in_list_order_and_the_last_page_stays(mine, three):
    """An op on a page deleted earlier is moot; the last page left stays, and says why."""
    ops = [_delete(0), _rotate(0, 90), _delete(1), _delete(2)]

    response = _export(mine, three, ops)

    assert_equal(response.headers[_SKIPPED], "3", "the edits left out")
    assert_equal(_lines(_opened(response)), [[_HEADER, "Third page"]], "the page that stays")
    render = f"/api/documents/{three['id']}/render"
    skipped = mine.post(render, json={"edits": ops, "scale": 1, "regions": []}).json()[
        "skipped"
    ]
    said = [(s["edit"], s["code"], s["detail"]) for s in skipped]
    assert_equal(said, [(3, "last_page", words.sentence("last_page"))], "why it was left out")


def test_pages_are_the_final_order_and_may_not_name_a_page_an_edit_deleted(mine, three):
    """The browser sends the order it shows, so a deleted page there is a request at odds."""
    moved = _export(mine, three, [_move(0, None)], pages=[0, 2])
    assert_equal(
        [lines[-1] for lines in _lines(_opened(moved))],
        ["First page", "Third page"],
        "pages asked, in the order asked",
    )

    assert_problem(_export(mine, three, [_delete(1)], pages=[0, 1]), "invalid_request", 400)


def test_a_redaction_is_read_where_the_page_ops_put_its_page(mine, three):
    """Page 0's header, redacted, moved last and turned: read there, and gone."""
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
    """The page goes, and its text with it: nothing is left to check."""
    response = _export(mine, three, [_redact(span_starting(three, 1, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], [_HEADER, "First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


@pytest.fixture(scope="module")
def filled_in() -> bytes:
    """A form filled in with a comment: "Name:" on the page, the answer a comment beside it.

    The comment draws its own words, which the upload lists as text like the page's.
    """
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
    """The edit takes the comment with the words it draws: it can carry them."""
    doc = upload(mine, filled_in).json()
    answer = span_starting(doc, 0, "Jane Doe")

    response = _export(mine, doc, [{**edit, "span_id": answer["id"]}])

    assert_equal(_lines(_opened(response)), [lines], "the page's lines")


def test_a_split_of_a_tagged_file_says_the_tags_went_with_the_pages_left_out(mine, tagged):
    """The tags point at every page, so leaving one out drops them: never silently."""
    doc = upload(mine, Path(tagged).read_bytes()).json()

    response = _export(mine, doc, [], pages=[0])

    said = json.loads(response.headers[_NOTICES])
    expected = [("file", "tags_dropped", words.sentence("tags_dropped"))]
    got = [(n["kind"], n["code"], n["detail"]) for n in said]
    assert_equal(got, expected, "what came out other than asked")
    assert_equal(_opened(response).page_count, 1, "pages in the file")


@pytest.fixture(scope="module")
def described(three_pages) -> bytes:
    """The three pages, saying what made them and when, as most files do."""
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
    """Every export is squidpdf's rewrite of the file: its Info says so, not its pages.

    Its other Info entries stay the file's own: Creator names the program that made it.
    """
    doc = upload(mine, described).json()
    first = span_starting(doc, 0, "First")
    edits = [{"kind": "replace", "span_id": first["id"], "text": "One"}] if edited else []
    asked_at = datetime.now(UTC).replace(microsecond=0)  # a PDF date keeps whole seconds

    response = _export(mine, doc, edits, pages)

    saved = _opened(response)
    said = [_info(saved, key) for key in ("Producer", "Title", "Creator")]
    assert_equal(said, ["squidpdf", "Lease", "Writer"], "what the Info holds")
    changed_at = _changed_at(saved).timestamp()
    assert_at_least(changed_at, asked_at.timestamp(), "when the Info says it changed")
    assert_at_most(changed_at, time.time(), "when the Info says it changed")


def _odd_info(shape: str) -> bytes:
    """A page whose Info isn't the object of its own the format asks for.

    The upload takes each, so the export must too. The one in the trailer itself says "Lease".
    """
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "First page", fontname="helv", fontsize=12)
    if shape == "in the trailer":
        doc.xref_set_key(-1, "Info", "<</Title(Lease)/Producer(Printer)>>")
    if shape == "leading nowhere":
        doc.xref_set_key(-1, "Info", "99 0 R")
    if shape == "not a dictionary":
        text = doc.get_new_xref()
        doc.update_object(text, "(just words)")
        doc.xref_set_key(-1, "Info", f"{text} 0 R")
    return doc.tobytes()


@pytest.mark.parametrize(
    ("shape", "title"),
    [("in the trailer", "Lease"), ("leading nowhere", "null"), ("not a dictionary", "null")],
)
def test_an_info_kept_oddly_still_exports_and_names_squidpdf(mine, shape, title):
    """An Info in the trailer itself keeps its entries; one that holds none is made anew."""
    doc = upload(mine, _odd_info(shape)).json()

    saved = _opened(_export(mine, doc, []))

    said = [_info(saved, key) for key in ("Producer", "Title")]
    assert_equal(said, ["squidpdf", title], "what the Info holds")


_OWNER_PASSWORD = "owner"
_AES_256 = 5  # pymupdf.PDF_ENCRYPT_AES_256, which its type stubs leave out
# PyMuPDF's PDF_PERM_PRINT and PDF_PERM_ACCESSIBILITY: print it, and read it aloud.
_MAY_PRINT_AND_READ_ALOUD = 4 | 512
_AS_OWNER = 4  # what `authenticate` answers for the owner's password
# The file's Encrypt dictionary, as its trailer keeps it: the method, the permissions (P), and
# whether its XMP is encrypted with the rest.
_PROTECTION_KEYS = ("Filter", "V", "R", "Length", "P", "EncryptMetadata")


def _owner_only(doc: pymupdf.Document) -> bytes:
    """`doc` behind an owner password alone, asking a viewer not to change or copy it.

    No user password, so anyone opens it, squidpdf included. Some permissions are off,
    so a download that came out with every one on would show.
    """
    allowed = _MAY_PRINT_AND_READ_ALOUD
    return doc.tobytes(encryption=_AES_256, owner_pw=_OWNER_PASSWORD, permissions=allowed)


@pytest.fixture(scope="module")
def owner_only(three_pages) -> bytes:
    """The three pages, behind an owner password alone."""
    return _owner_only(pymupdf.open(stream=three_pages, filetype="pdf"))


def _protection_of(pdf_bytes: bytes) -> tuple[list[str], bool]:
    """How a file is protected, and whether the owner password still opens it as owner."""
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    encrypt_entries = [_protection_entry(pdf, key) for key in _PROTECTION_KEYS]
    return encrypt_entries, pdf.authenticate(_OWNER_PASSWORD) == _AS_OWNER


def _protection_entry(pdf: pymupdf.Document, key: str) -> str:
    """The file's protection's `key`, read through its object when it's kept in one."""
    value_type, value = pdf.xref_get_key(-1, f"Encrypt/{key}")  # -1: the trailer
    # In the Encrypt dictionary itself.
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
    """The download keeps the file's method, its permissions and its owner password.

    Though it asks not to be changed, it is edited: the request goes on to the next reader.
    """
    doc = upload(mine, owner_only).json()
    second = span_starting(doc, 1, "Second")
    edits = [{"kind": "replace", "span_id": second["id"], "text": "Page two"}]

    response = _export(mine, doc, edits, pages)

    lines = [line for page in _lines(_opened(response)) for line in page]
    assert_in("Page two", lines, "the download's lines")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(owner_only), "how the download is protected")


def test_a_redaction_in_a_file_with_only_an_owner_password_is_checked_and_kept_protected(
    mine, owner_only
):
    """The check reads the download as any viewer opens it, without the password."""
    doc = upload(mine, owner_only).json()

    response = _export(mine, doc, [_redact(span_starting(doc, 0, "First"))])

    assert_not_in("First page", _lines(_opened(response))[0], "the first page's lines")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(owner_only), "how the download is protected")


_AES_128 = 4  # pymupdf.PDF_ENCRYPT_AES_128: its key, as RC4's, is made from the file's ID
_FILE_ID = re.compile(rb"/ID\s*\[[^\]]*\]")  # the trailer's ID, both its parts


@pytest.fixture(scope="module")
def owner_only_without_an_id(three_pages) -> bytes:
    """The three pages, behind an owner password alone, with no ID in the trailer.

    As some programs write one: its key made from an empty first ID, the ID then left out,
    so a reader takes the missing ID as that empty one. Saved without object streams, the
    trailer is plain text, and taking the ID out of it moves no object.
    """
    doc = pymupdf.open(stream=three_pages, filetype="pdf")
    doc.xref_set_key(-1, "ID", "[<> <>]")  # -1: the trailer; MuPDF keeps the empty first part
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
    """Its key comes from the missing ID: a fresh ID written whole would lock it for good."""
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
    """The three pages, running a script as they open, behind an owner password alone.

    The upload drops the script, so its stored original is the file written again.
    """
    doc = pymupdf.open(stream=three_pages, filetype="pdf")
    script = doc.get_new_xref()
    doc.update_object(script, "<</S/JavaScript/JS(app.alert('opened'))>>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{script} 0 R")
    return _owner_only(doc)


def test_a_file_with_only_an_owner_password_keeps_its_protection_when_its_upload_drops_a_script(
    mine, owner_only_with_a_script
):
    """The upload's rewrite keeps the protection, so a download with no edit has it too."""
    doc = upload(mine, owner_only_with_a_script).json()

    response = _export(mine, doc, [])

    notices = [notice["type"] for notice in doc["notices"]]
    assert_in("active_content_dropped", notices, "what the upload said")
    said = _protection_of(response.content)
    expected = _protection_of(owner_only_with_a_script)
    assert_equal(said, expected, "how the download is protected")


_FIXTURES = Path(__file__).parents[2] / "fixtures"
# Files behind an owner password alone that keep their XMP plain (`fixtures/plain_xmp.py`):
# AES-128, AES-256, then AES-128 with its page, then its catalog's PieceInfo, naming it too.
_PLAIN_XMP = [
    "plain_xmp_aes128.pdf",
    "plain_xmp_aes256.pdf",
    "plain_xmp_shared.pdf",
    "plain_xmp_pieceinfo.pdf",
]
_PLAIN_XMP_TITLE = "Quarterly report"  # their Info's title, and their page's line


def _xmp_objects_in(pdf: pymupdf.Document) -> list[int]:
    """Each object in the file that's XMP, by number, whatever names it."""
    return [
        xref
        for xref in range(1, pdf.xref_length())
        if pdf.xref_get_key(xref, "Type") == ("name", "/Metadata")
    ]


@pytest.mark.parametrize("name", _PLAIN_XMP)
def test_a_protected_files_plain_xmp_is_left_out_of_the_download_and_said(mine, name):
    """The library may read and write it as encrypted, so it would come out garbled.

    The Info stays.
    """
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


# An entry's value as a dictionary's source writes it, after its key: a dictionary of
# dictionaries (CF), a string, a name, or a number or a true or false after a space.
_ENTRY_VALUE = r"<<(?:[^<>]|<<[^<>]*>>)*>>|<[0-9A-Fa-f]*>|\([^)]*\)|/[^/<>()\[\] ]+| [^/<>]+"


def _with_update(sent: bytes, objects: dict[int, str]) -> bytes:
    """`sent` with an update writing each of `objects`, by its number, over what it held.

    Appended as an edit in place appends one: the objects, then a table and a trailer
    naming only what changed, and where the table before it is (Prev).
    """
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    written = [f"{number} 0 obj\n{source}\nendobj\n" for number, source in objects.items()]
    offsets = list(accumulate(map(len, written[:-1]), initial=len(sent)))
    table = "".join(
        f"{number} 1\n{at:010d} 00000 n \n" for number, at in zip(objects, offsets, strict=True)
    )
    size = max(pdf.xref_length(), max(objects) + 1)
    trailer = re.sub(r"/Size \d+", f"/Size {size}", pdf.pdf_trailer(compressed=True))
    entries = trailer.removesuffix(">>")  # left open, for Prev to join
    previous = re.findall(rb"startxref\s+(\d+)", sent)[-1].decode()
    table_at = offsets[-1] + len(written[-1])
    update = (
        f"{''.join(written)}xref\n{table}trailer\n{entries}/Prev {previous}>>\n"
        f"startxref\n{table_at}\n%%EOF\n"
    )
    return sent + update.encode()


def _with_entry_apart(sent: bytes, entry: str, *, references: int) -> bytes:
    """`sent` with an update keeping its protection's `entry` in an object of its own.

    Reached through `references` objects, each naming the next, the last holding it.
    """
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = int(pdf.xref_get_key(-1, "Encrypt")[1].split()[0])  # -1: the trailer
    apart = pdf.xref_length()  # the next free object number
    source = pdf.xref_object(encrypt, compressed=True)
    value = re.findall(rf"/{entry}({_ENTRY_VALUE})", source)[0]
    source = source.replace(f"/{entry}{value}", f"/{entry} {apart} 0 R", 1)
    last = apart + references - 1
    naming = {number: f"{number + 1} 0 R" for number in range(apart, last)}
    return _with_update(sent, {encrypt: source, **naming, last: value})


def _entries_apart(pdf: pymupdf.Document) -> list[str]:
    """Each entry of the file's protection kept as an object of its own."""
    encrypt = int(pdf.xref_get_key(-1, "Encrypt")[1].split()[0])  # -1: the trailer
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
    """An entry kept in an object of its own is read through it, and written in place.

    A reader needs it before it can decrypt anything, so the download opens without a
    password and its page reads; the file's plain XMP is still left out, and said.
    """
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


# The AES-128 fixture's one crypt filter (StdCF), and its crypt filters (CF), as its Encrypt
# dictionary writes them.
_STD_CF = "<</AuthEvent/DocOpen/CFM/AESV2/Length 16>>"
_CRYPT_FILTERS = f"/CF<</StdCF{_STD_CF}>>"


def _with_crypt_filter_apart(sent: bytes, *, crypt_filters: str, objects: list[str]) -> bytes:
    """`sent` with its crypt filters written as `crypt_filters`, naming `objects`.

    The objects take the next free numbers, which the sources name as `{a}` and `{b}`.
    """
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = int(pdf.xref_get_key(-1, "Encrypt")[1].split()[0])  # -1: the trailer
    a = pdf.xref_length()  # the next free object number
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
    """It goes in place in CF, wherever else it's named, so the download opens."""
    aes_128 = (_FIXTURES / _PLAIN_XMP[0]).read_bytes()
    sent = _with_crypt_filter_apart(aes_128, crypt_filters=crypt_filters, objects=objects)
    uploaded = upload(mine, sent)

    assert_equal(uploaded.status_code, 201, uploaded.text[:200])
    response = _export(mine, uploaded.json(), [])
    saved = _opened(response)
    assert_in(_PLAIN_XMP_TITLE, saved[0].get_text(), "the download's page")
    crypt_filter, _source = saved.xref_get_key(-1, "Encrypt/CF/StdCF")  # -1: the trailer
    assert_equal(crypt_filter, "dict", "how the download keeps its crypt filter")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


def _with_objects_named_beside_protection(sent: bytes, *, links: str, depth: int) -> bytes:
    """`sent` with its Encrypt dictionary naming, under a key no reader reads, a run of objects.

    `depth` objects, each naming the next by `links`, which writes it as `{next}`.
    """
    pdf = pymupdf.open(stream=sent, filetype="pdf")
    encrypt = int(pdf.xref_get_key(-1, "Encrypt")[1].split()[0])  # -1: the trailer
    first = pdf.xref_length()  # the next free object number
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
    """Only what a reader reads to make the key goes in place, so the rest can't hold it up.

    Followed whole, objects naming the next twice double at each one, and a long run goes
    deeper than Python lets calls go.
    """
    aes_128 = (_FIXTURES / _PLAIN_XMP[0]).read_bytes()
    sent = _with_objects_named_beside_protection(aes_128, links=links, depth=depth)
    uploaded = upload(mine, sent)

    assert_equal(uploaded.status_code, 201, uploaded.text[:200])
    response = _export(mine, uploaded.json(), [])
    saved = _opened(response)
    assert_in(_PLAIN_XMP_TITLE, saved[0].get_text(), "the download's page")
    said = _protection_of(response.content)
    assert_equal(said, _protection_of(sent), "how the download is protected")


_RC4_128 = 3  # pymupdf.PDF_ENCRYPT_RC4_128: protection (V2) with no crypt filters


def _flag_written_as(pdf: pymupdf.Document, *, encryption: int, flag: bytes) -> bytes:
    """`pdf` behind `encryption` and an owner password, its EncryptMetadata written as `flag`.

    Saved without object streams, the trailer is plain text after every object, so a
    longer flag moves none.
    """
    protected = pdf.tobytes(encryption=encryption, owner_pw=_OWNER_PASSWORD, use_objstms=False)
    return protected.replace(b"/EncryptMetadata true", b"/EncryptMetadata " + flag, 1)


def _flag_naming_the_next_object(pdf: pymupdf.Document) -> bytes:
    """`pdf` behind AES-128, its EncryptMetadata naming the object the upload adds first.

    The file holds no such object, so a reader takes the flag as no flag.
    """
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
    """Only XMP the file keeps plain is left out. The protection comes out as it came."""
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
    """The body is the file, so what was left out travels beside it, by place in the list."""
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


@pytest.mark.parametrize(
    "asked", [{}, {"range": [0, 4]}], ids=["all of it", "some words of it"]
)
def test_an_edit_after_a_redaction_on_the_same_text_fails_the_export(mine, doc, asked):
    """Redaction wins: the list's order must not bring the text back into the file."""
    span = next(s for s in doc["spans"] if s["page"] == 1)
    redact = {"kind": "redact", "span_id": span["id"], **asked}
    replace = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [redact, replace])

    assert_problem(response, "redaction_conflict", 422)


# A line drawn by a quote, which moves to the next line first: MuPDF's erase rewrites one so
# the line lands elsewhere, above where it was, its letters still drawn.
_QUOTED = "BT /F 12 Tf 14 TL 72 114 Td {spacing}(Acct 1234 Name) {quote} ET"


@pytest.mark.parametrize(
    "drawing",
    [
        _QUOTED.format(spacing="6 1 ", quote='"'),
        _QUOTED.format(spacing="", quote="'"),
        # Drawn twice over, as for bold: the erase takes the copy, and the line still moves.
        _QUOTED.format(spacing="", quote="'")
        + " BT /F 12 Tf 72.4 100 Td (Acct 1234 Name) Tj ET",
        # Marked content that says other words than its glyphs.
        "/Span <</ActualText (Card number)>> BDC "
        "BT /F 12 Tf 14 TL 72 114 Td (4421 8812) ' ET EMC",
    ],
    ids=["a double quote", "a single quote", "drawn twice over", "said in other words"],
)
def test_a_line_the_erase_moves_off_its_box_downloads_nothing(mine, drawing):
    """The check reads every glyph the page draws, on it or off it: none slips away."""
    doc = upload(mine, drawn_page(drawing)).json()
    span = doc["spans"][0]
    body = {"edits": [_redact(span)], "scale": 1, "regions": [around(span)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(span)])

    verified = rendered["redactions"][span["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


def _dense_line(path: Path) -> bytes:
    """The sample generator's page, whose lines spell one of a line's words as it goes."""
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
    """Only glyphs count, where they're drawn: what's left beside them spells nothing."""
    doc = upload(mine, sent(tmp_path / "dense.pdf")).json()
    span = next(each for each in doc["spans"] if each["text"].startswith(starts))
    body = {"edits": [_redact(span)], "scale": 1, "regions": [around(span)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(span)])

    assert_equal(rendered["redactions"][span["id"]]["verified"], True, "render's check")
    assert_equal(response.status_code, 200, "the download")


# Three lines, apart, one under another.
_THREE_LINES = (
    "BT /F 12 Tf 72 150 Td ({}) Tj ET BT /F 12 Tf 72 134 Td ({}) Tj ET "
    "BT /F 12 Tf 72 118 Td ({}) Tj ET"
)


def test_a_line_whose_letters_the_erase_misses_downloads_nothing(mine, tmp_path):
    """A width list with no first code sets its letters at no width, its last ones on its edge.

    Read past the rounding its box is kept to, they're its own, and drawn still: refused.
    """
    path = named_with_widths(
        str(tmp_path / "arial.pdf"), "Liberation Sans Regular", base_font="Arial"
    )
    named = pymupdf.open(path)
    [(font_object, *_rest)] = named[0].get_fonts()
    named.xref_set_key(font_object, "FirstChar", "null")
    doc = upload(mine, named.tobytes()).json()
    [span] = doc["spans"]
    body = {"edits": [_redact(span)], "scale": 1, "regions": [around(span)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(span)])

    verified = rendered["redactions"][span["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


# Two lines drawn by a quote after a negative leading: MuPDF's erase swaps them. "Zed",
# redacted too, is level with where the second line was, so an erase reached that line.
_SWAPPED = (
    "BT /F 12 Tf -20 TL 72 60 Td (Acct 1234 Name) ' ET "
    "BT /F 12 Tf -80 TL {x} 40 Td (4321 Acct Name) ' ET "
    "BT /F 12 Tf 300 120 Td (Zed) Tj ET"
)


@pytest.mark.parametrize(
    ("asked", "x"), [(range(5, 9), 98.672), (None, 72)], ids=["some words of it", "all of it"]
)
def test_lines_an_erase_swaps_download_nothing(mine, asked, x):
    """The words it was to take land on the other line, still drawn: refused."""
    doc = upload(mine, drawn_page(_SWAPPED.format(x=x))).json()
    account, zed = span_starting(doc, 0, "Acct 1234"), span_starting(doc, 0, "Zed")
    redact = _redact(account) if asked is None else _redact_words(account, asked)
    edits = [redact, _redact(zed)]
    body = {"edits": edits, "scale": 1, "regions": [{"page": 0}]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][account["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


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
    """Read as the page spells it: "Ann" in "Annual", or "12 34", is other text moved.

    A gap kerned wide enough is a space to the reader, as one drawn is.
    """
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


# A line, a span level with it further along, as a résumé sets a date, and a line below.
_LEVEL_AND_BELOW = (
    "BT /F 12 Tf 72 100 Td (Acct 1234) Tj ET BT /F 12 Tf 300 100 Td (Spring 2024) Tj ET "
    "BT /F 12 Tf 72 60 Td (Other words) Tj ET"
)
_PAGE_HEIGHT = 842  # the page `drawn_page` draws on, to place its lines from the top


_STILL = (0.0, 0.0)


def _along_further_along(origin: Place) -> Place:
    """The span further along the redacted one's line, set along it."""
    return (2.0, 0.0) if origin[0] >= 300 else _STILL


def _off_further_along(origin: Place) -> Place:
    """The span further along the redacted one's line, set off it."""
    return (0.0, 3.0) if origin[0] >= 300 else _STILL


def _along_below(origin: Place) -> Place:
    """The line below, set along it."""
    return (2.0, 0.0) if origin[1] > _PAGE_HEIGHT - 80 else _STILL


def test_the_rest_of_a_redacted_line_set_along_it_downloads_saying_so(app, mine, monkeypatch):
    """A span further along the line the erase set along it: the rest of it, said; it goes."""
    moving = MovingAfterAnErase(_along_further_along)
    monkeypatch.setattr(app.state, "documents", store.FolderStore(moving))
    doc = upload(mine, drawn_page(_LEVEL_AND_BELOW)).json()
    account = span_starting(doc, 0, "Acct")
    body = {"edits": [_redact(account)], "scale": 1, "regions": [{"page": 0}]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(account)])

    said = rendered["redactions"][account["id"]]
    moved = words.render(Message("rest_of_line_moved", {"text": account["text"], "page": 1}))
    assert_equal((said["verified"], said["message"]), (True, moved), "what render says")
    assert_equal(response.status_code, 200, "the download")


@pytest.mark.parametrize(
    "moves", [_off_further_along, _along_below], ids=["off its line", "another line"]
)
def test_other_text_an_erase_moves_refuses_the_download_saying_so(
    app, mine, monkeypatch, moves
):
    """Only the rest of a redacted span's own line may move, and saying so: nothing else."""
    monkeypatch.setattr(app.state, "documents", store.FolderStore(MovingAfterAnErase(moves)))
    doc = upload(mine, drawn_page(_LEVEL_AND_BELOW)).json()
    account = span_starting(doc, 0, "Acct")
    body = {"edits": [_redact(account)], "scale": 1, "regions": [{"page": 0}]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(account)])

    said = rendered["redactions"][account["id"]]
    facts = {"text": account["text"], "page": 1}
    moved = words.render(Message("moved_other_text", facts))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)
    assert_equal(response.json()["params"], facts, "the words and the page, unsaid")


# A quote moves to the next line first: MuPDF's erase sets the line elsewhere, beside the
# redacted one where it lands at 74, losing letters to its erase.
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
    """Its words are gone, but other text on the page moved: refused, and said as it is."""
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    redact = _redact(account) if asked is None else _redact_words(account, asked)
    body = {"edits": [redact], "scale": 1, "regions": [around(account)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
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
    """MuPDF's driver, whose erase rewrites the page's drawing as `script` says instead."""

    driver: Any  # MuPDF's, whose open document the erase rewrites
    script: tuple[tuple[bytes, bytes], ...]  # each piece of the drawing, and what it becomes

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        """The page's drawing rewritten; every box read clear, as an erase that worked says."""
        doc = self.driver.doc
        [contents] = doc[page].get_contents()
        drawing = doc.xref_stream(contents)
        for piece, becomes in self.script:
            drawing = drawing.replace(piece, becomes)
        doc.update_stream(contents, drawing)
        return ["" for _box in boxes]

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the real driver answers it."""
        return getattr(self.driver, name)


@dataclass(frozen=True, slots=True)
class _Rewriting:
    """A driver factory for the store: MuPDF's, its erase rewriting the drawing by `script`.

    A worker opens the file, so the factory is sent there: no closure.
    """

    script: tuple[tuple[bytes, bytes], ...]

    def __call__(self, path: str) -> PdfDriver:
        """The driver for `path`."""
        # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
        return cast(PdfDriver, _RewritingDriver(open_driver(path), self.script))


def test_words_an_erase_sets_along_their_line_level_with_others_download_nothing(
    app, mine, monkeypatch
):
    """Set further along, where another column's same words were, the words are still drawn."""
    drawing = (
        "BT /F 12 Tf 20 100 Td (Acct ) Tj ET BT /F 12 Tf 46.676 100 Td (1234) Tj ET "
        "BT /F 12 Tf 73.364 100 Td ( Name) Tj ET "
        "BT /F 12 Tf 300 100 Td (Ref ) Tj ET BT /F 12 Tf 322.008 100 Td (1234) Tj ET"
    )
    # An erase that takes the other column's number, and sets this one further along, drawn.
    script = (
        (b"46.676 100 Td (1234)", b"200 100 Td (1234)"),
        (b"322.008 100 Td (1234)", b"322.008 100 Td ()"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    body = {"edits": edits, "scale": 1, "regions": [{"page": 0}]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][account["id"]]["verified"]
    assert_equal((verified, response.status_code), (False, 422), "render's answer, export's")


# Helvetica at 12: "Acct " is 26.676 wide, each digit 6.672.
_ACCT_LINE = (
    "BT /F 12 Tf 20 100 Td (Acct ) Tj ET BT /F 12 Tf 46.676 100 Td (1234) Tj ET "
    "BT /F 12 Tf 73.364 100 Td ( Name) Tj ET "
)
_ERASED = (b"46.676 100 Td (1234)", b"46.676 100 Td ()")  # the number erased, and only it
_OTHER = "BT /F 12 Tf 20 60 Td (Other) Tj ET "  # drawn between, so moved runs stay apart
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
    """One redacted line's rest moves by one shift, in order, onto nothing: else it's moved."""
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


def test_the_spans_on_a_whole_redacted_line_swapped_refuse_the_download_as_moved(
    app, mine, monkeypatch
):
    """For all of a span, its line's rest is the other spans on it: swapped, they're moved."""
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
    """Render and export of `edits` both refuse them, saying other text moved."""

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [{"page": 0}]},
    ).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][span["id"]]
    moved = words.render(Message("moved_other_text", {"text": text, "page": 1}))
    assert_equal((said["verified"], said["message"]), (False, moved), "what render says")
    assert_problem(response, "redaction_moved", 422)


@pytest.mark.parametrize(
    ("to", "said"),
    [(b"2046.688", "rest_of_line_removed"), (b"575", "rest_of_line_moved")],
    ids=["all of it off the page, removed", "some of it still on it, moved"],
)
def test_the_rest_of_a_line_set_off_the_page_is_said_removed_unless_some_still_shows(
    app, mine, monkeypatch, to, said
):
    """What the reader sees is said: the rest gone, or moved where some of it still shows."""
    # The words first on their line, and all the rest of it set along, the page 595 wide.
    drawing = "BT /F 12 Tf 20 100 Td (1234) Tj ET BT /F 12 Tf 46.688 100 Td ( Name) Tj ET"
    script = ((b"20 100 Td (1234)", b"20 100 Td ()"), (b"46.688 100 Td", to + b" 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = span_starting(doc, 0, "1234")
    edits = [_redact_words(line, range(0, 4))]

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [{"page": 0}]},
    ).json()
    response = _export(mine, doc, edits)

    verdict = rendered["redactions"][line["id"]]
    message = words.render(Message(said, {"text": "1234", "page": 1}))
    assert_equal((verdict["verified"], verdict["message"]), (True, message), "what render says")
    assert_equal(response.status_code, 200, "the download")


@pytest.mark.parametrize(
    "drawing",
    [
        # With no leading, MuPDF's erase of some words drops the T* and sets "Zed" along, onto
        # "Note": the line would read broken.
        "BT /F 12 Tf 0 TL 72 100 Td [-10000 (Acct 1234 Name)] TJ T* (Zed) Tj ET "
        "BT /F 12 Tf 280.7 100 Td (Note) Tj ET",
    ],
    ids=["onto other text"],
)
def test_text_on_the_redacted_line_moved_but_not_as_its_rest_refuses_the_download_as_moved(
    mine, drawing
):
    """Other glyphs than those redacted moved: refused, said as moved, not as the words left."""
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    body = {"edits": edits, "scale": 1, "regions": [around(account)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
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
        # A quote moves the line, its words still drawn: all of it, a space at its end.
        (
            "BT /F 12 Tf 14 TL 20 114 Td (Acct 1234 ) ' ET",
            [None],
            "not_redacted",
            "redaction_failed",
        ),
        # Two runs, the words gone but the line below moved: both runs named.
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
    """Each run, an ellipsis between, its spaces aside: the words the user redacted."""
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [
        _redact(account) if each is None else _redact_words(account, each) for each in asked
    ]
    body = {"edits": edits, "scale": 1, "regions": [around(account)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
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
    """Drawn elsewhere, they're the words still drawn, though another line's letters went."""
    drawing = (
        "BT /F 12 Tf 20 100 Td (1234) Tj ET "
        "BT /F 12 Tf 20 60 Td (Page 1 of 2, item 3 of 4) Tj ET"
    )
    # An erase that sets the number elsewhere, drawn, and takes the other line instead.
    script = (
        (b"20 100 Td (1234)", b"200 140 Td (1234)"),
        (b"20 60 Td (Page 1 of 2, item 3 of 4)", b"20 60 Td ()"),
    )
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")
    body = {"edits": [_redact(number)], "scale": 1, "regions": [{"page": 0}]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(number)])

    verified = rendered["redactions"][number["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


@pytest.mark.parametrize(
    ("drawing", "script", "asked"),
    [
        # The number set elsewhere, drawn, while another line holding 1, 2, 3, 4 went.
        (
            _ACCT_LINE + "BT /F 12 Tf 20 60 Td (Page 1 of 2, item 3 of 4) Tj ET",
            (
                (b"46.676 100 Td (1234)", b"200 140 Td (1234)"),
                (b"20 60 Td (Page 1 of 2, item 3 of 4)", b"20 60 Td ()"),
            ),
            range(5, 9),
        ),
        # Two words redacted: the first set along the line, where the rest's same word went.
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
    """A run spelling any word redacted is that word still drawn: refused as not removed."""
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = doc["spans"][0]
    edits = [_redact_words(line, asked)]

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [{"page": 0}]},
    ).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][line["id"]]["verified"]
    assert_equal(verified, False, "whether render said the words were gone")
    assert_problem(response, "redaction_failed", 422)


def test_the_same_words_set_along_the_line_are_the_words_still_drawn(mine):
    """MuPDF's erase sets "ref 1234" along: it spells the number redacted, refused as such."""
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
# A line 11 points over `_ACCT_LINE`, tighter than solid: its "gy" already touch "Acct" below,
# and its "o"s, further along, reach down to nothing set under them.
_TIGHT_ABOVE = "BT /F 12 Tf 20 111 Td (gy oooooooooooo) Tj ET "
# The rest of the line, " Name", set along onto where "ZED" is drawn.
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
    """Letters overlap by their boxes as drawn, descenders too: the page would read broken."""
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(_ONTO)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]
    _assert_refused_as_moved(mine, doc, account, edits, text="1234")


def test_the_rest_of_a_line_set_onto_a_letter_only_the_words_touched_refuses_as_moved(
    app, mine, monkeypatch
):
    """The words' letters touching it before allow the rest nothing: they're gone."""
    # "pp ay", set solid over an "Å" the "p"s' tails touch: the "y" set where they were.
    drawing = (
        "BT /F 12 Tf 20 112 Td (pp) Tj ET BT /F 12 Tf 33.344 112 Td ( ay) Tj ET "
        r"BT /F 12 Tf 30 100 Td (\305) Tj ET"
    )
    script = ((b"20 112 Td (pp)", b"20 112 Td ()"), (b"33.344 112 Td", b"20 112 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    line = span_starting(doc, 0, "pp")
    _assert_refused_as_moved(mine, doc, line, [_redact_words(line, range(0, 2))], text="pp")


# "Åcc 1234 Åsy", whose "g" above, when there is one, touches its first ring: a pair that met.
_RING_LINE = (
    r"BT /F 12 Tf 20 100 Td (\305cc ) Tj ET BT /F 12 Tf 43.34 100 Td (1234) Tj ET "
    r"BT /F 12 Tf 70.028 100 Td ( \305sy) Tj ET BT /F 12 Tf 20 112.5 Td (g) Tj ET "
)
# The number erased, and " Åsy" set where it was: its ring at 46.676, its "y" at 60.68.
_RING_MOVE = ((b"43.34 100 Td (1234)", b"43.34 100 Td ()"), (b"70.028 100 Td", b"43.34 100 Td"))


@pytest.mark.parametrize(
    "drawing",
    [
        _RING_LINE + "BT /F 12 Tf 46.376 112.5 Td (_) Tj ET",
        _RING_LINE.replace("20 112.5", "20 110") + "BT /F 12 Tf 47.676 110 Td (.) Tj ET",
        # Below, the first "l" touches the line's "y": the second, as deep, never met it.
        _RING_LINE.replace(r"(\305cc )", r"(\305yc )")
        + "BT /F 12 Tf 29 89 Td (l) Tj ET BT /F 12 Tf 61.18 89 Td (l) Tj ET",
    ],
    ids=["a whole underscore", "a whole full stop", "a letter below, as deep as another met"],
)
def test_the_rest_of_a_line_set_onto_a_letter_that_never_met_it_refuses_as_moved(
    app, mine, monkeypatch, drawing
):
    """Another of that line's letters touching it, at its start, allows this one nothing."""
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
    """Lines that run side by side, no two of their letters touching: any overlap is landing.

    A tall letter of one and a deep one of the other, apart, aren't letters that met.
    """
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
        # One "_" stretched over all of the line, low enough to touch its capitals.
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
    """Clear of other letters, or over its own copy set along with it: the rest, said."""
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [{"page": 0}]},
    ).json()
    response = _export(mine, doc, edits)

    said = rendered["redactions"][account["id"]]
    moved = words.render(Message("rest_of_line_moved", {"text": "1234", "page": 1}))
    assert_equal((said["verified"], said["message"]), (True, moved), "what render says")
    assert_equal(response.status_code, 200, "the download")


@dataclass(frozen=True, slots=True, eq=False)
class _UnboxedDriver:
    """A driver whose glyphs' boxes can't be read for every glyph it draws."""

    driver: Any  # the driver it answers for, but its glyphs' boxes
    fewer: bool  # one box short of the glyphs, as a reading out of step; else none read

    def drawn_glyph_boxes(self, page: int) -> list[Rect] | None:
        """One box short of the glyphs drawn, or none."""
        return self.driver.drawn_glyph_boxes(page)[:-1] if self.fewer else None

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the driver answers it."""
        return getattr(self.driver, name)


@dataclass(frozen=True, slots=True)
class _Unboxed:
    """A driver factory for the store: `_Rewriting`'s, its glyphs' boxes not all read."""

    script: tuple[tuple[bytes, bytes], ...]
    fewer: bool

    def __call__(self, path: str) -> PdfDriver:
        """The driver for `path`."""
        rewriting = _RewritingDriver(open_driver(path), self.script)
        # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
        return cast(PdfDriver, _UnboxedDriver(rewriting, self.fewer))


@pytest.mark.parametrize("fewer", [True, False], ids=["a box short", "none read"])
def test_a_rest_whose_letters_can_t_be_boxed_refuses_saying_it_couldn_t_be_checked(
    app, mine, monkeypatch, fewer
):
    """Set along, its landing can't be read: nothing downloads, and that's what's said."""
    script = (_ERASED, (b"73.364 100 Td", b"63.364 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Unboxed(script, fewer)))
    doc = upload(mine, drawn_page(_ACCT_LINE)).json()
    account = span_starting(doc, 0, "Acct")
    edits = [_redact_words(account, range(5, 9))]

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [{"page": 0}]},
    ).json()
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
    """ "Zed", after "Name", set before it, where the words went: the line's order changed."""
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
    """What's before it stays, what's after it moves along by one shift: the rest, said."""
    drawing = (
        "BT /F 12 Tf 20 100 Td (Head |) Tj ET BT /F 12 Tf 100 100 Td (1234) Tj ET "
        + _OTHER
        + "BT /F 12 Tf 400 100 Td (2024) Tj ET"
    )
    script = ((b"100 100 Td (1234)", b"100 100 Td ()"), (b"400 100 Td", b"420 100 Td"))
    monkeypatch.setattr(app.state, "documents", store.FolderStore(_Rewriting(script)))
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": [_redact(number)], "scale": 1, "regions": [{"page": 0}]},
    ).json()
    response = _export(mine, doc, [_redact(number)])

    said = rendered["redactions"][number["id"]]
    moved = words.render(Message("rest_of_line_moved", {"text": "1234", "page": 1}))
    assert_equal((said["verified"], said["message"]), (True, moved), "what render says")
    assert_equal(response.status_code, 200, "the download")


def _on_zed_line(box: Rect) -> bool:
    """Whether an erase's box is on the line with "Zed", over the redacted one."""
    return box.y1 < _PAGE_HEIGHT - 120


def test_render_checks_a_redaction_after_every_erase_on_its_page_as_export_does(
    app, mine, monkeypatch
):
    """An erase on the page outside the regions drawn still runs: the answers agree."""
    # Erasing "Zed" sets the line below along.
    drawing = (
        "BT /F 12 Tf 72 100 Td (Acct 1234) Tj ET BT /F 12 Tf 72 140 Td (Zed) Tj ET "
        "BT /F 12 Tf 72 60 Td (Other words) Tj ET"
    )
    moving = MovingAfterAnErase(_along_below, when=_on_zed_line)
    monkeypatch.setattr(app.state, "documents", store.FolderStore(moving))
    doc = upload(mine, drawn_page(drawing)).json()
    account, zed = span_starting(doc, 0, "Acct"), span_starting(doc, 0, "Zed")
    edits = [_redact(account), _redact(zed)]

    rendered = mine.post(
        f"/api/documents/{doc['id']}/render",
        json={"edits": edits, "scale": 1, "regions": [around(account)]},
    ).json()
    response = _export(mine, doc, edits)

    verified = rendered["redactions"][account["id"]]["verified"]
    assert_equal((verified, response.status_code), (False, 422), "render's answer, export's")


def test_a_letter_whose_middle_sits_on_its_box_s_rounded_edge_goes_with_it(mine):
    """The box is kept to hundredths: the erase reaches as far past it as the check reads."""
    # An "i" in red starting inside the number's last letter: its middle 0.003 pt past the box.
    drawing = "BT /F 12 Tf 20 100 Td (1234) Tj ET BT 1 0 0 rg /F 12 Tf 45.361 100 Td (i) Tj ET"
    doc = upload(mine, drawn_page(drawing)).json()
    number = span_starting(doc, 0, "1234")

    saved = _opened(_export(mine, doc, [_redact(number)]))

    assert_equal(saved[0].get_text().split(), [], "the letters left on the page")


def test_a_redaction_pointing_at_nothing_fails_the_export(mine, doc):
    """Skipping it would send the text the user asked to remove."""
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
    """As one pointing at nothing: skipping it would send the words the user asked to remove."""
    doc = upload(mine, with_a_card_line()).json()
    card = span_starting(doc, 0, "Card")

    response = _export(mine, doc, [{"kind": "redact", "span_id": card["id"], "range": asked}])

    assert_problem(response, "bad_range", 422)
    said = words.sentence("bad_range").format(span_id=card["id"])
    assert_equal(response.json()["detail"], said, "what the user reads")


def _redact_words(span: dict, asked: range) -> dict:
    """An edit redacting the words of `span` that `asked` names, by code point."""
    return {"kind": "redact", "span_id": span["id"], "range": [asked.start, asked.stop]}


def _letters_drawn(pdf: pymupdf.Document) -> list[tuple[str, float, float]]:
    """Each letter the first page draws, and where its baseline starts, to 0.01 pt."""
    return [
        (char["c"], round(char["origin"][0], 2), round(char["origin"][1], 2))
        for block in pdf[0].get_text("rawdict")["blocks"]
        for line in block["lines"]
        for piece in line["spans"]
        for char in piece["chars"]
    ]


@pytest.mark.parametrize(
    ("ranges", "title"),
    [
        ([FIRST_NUMBER], "Card then 9918 ends"),
        ([FIRST_NUMBER, SECOND_NUMBER], "Card then ends"),
        # "4421 then" and "then 9918", which overlap: one run, from the first to the last.
        ([range(5, 14), range(10, 19)], "Card ends"),
    ],
    ids=["one number", "both numbers", "overlapping words"],
)
def test_words_of_a_line_redacted_go_and_the_rest_stays_where_it_was(mine, ranges, title):
    """Each run goes, from the page and from the title, and every other letter stays put."""
    _assert_words_go_and_the_rest_stays(mine, ranges, title)


class _At:
    """A clock stopped at 05:44:21, whose save's date writes "4421", the first number."""

    @staticmethod
    def now(tz: tzinfo | None = None) -> datetime:
        """That time, on the day this was written."""
        return datetime(2026, 11, 2, 5, 44, 21, tzinfo=tz)


def test_a_save_whose_date_spells_a_word_redacted_is_still_without_it(app, mine, monkeypatch):
    """The save's own date and id aren't the document's words: they can spell any digits."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the clock below reaches it
    monkeypatch.setattr(export, "datetime", _At)
    _assert_words_go_and_the_rest_stays(mine, [FIRST_NUMBER, SECOND_NUMBER], "Card then ends")


def _assert_words_go_and_the_rest_stays(mine, ranges: list[range], title: str) -> None:
    """Each of `ranges` of the card line goes, and every other letter stays put."""
    sent = with_a_card_line()
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
    for word in re.finditer(r"\S+", CARD_LINE):
        if taken.issuperset(range(word.start(), word.end())):
            assert_not_in(word[0], everything, "a word redacted, anywhere in the file")


def _glyphs_drawn(pdf: pymupdf.Document) -> str:
    """The letters the first page's drawing writes, in the order drawn, spaces aside."""
    letters = (chr(char[0]) for run in pdf[0].get_texttrace() for char in run["chars"])
    return "".join(letter for letter in letters if not letter.isspace())


_ACCOUNT = "BT /F 12 Tf 72 100 Td (Acct 1234 Name) Tj ET"  # a line with a number in it


@pytest.mark.parametrize(
    ("sent", "starts", "size", "asked", "drawn", "title"),
    [
        # Marked content that says its glyphs in another order: the glyphs "1234" go.
        (
            drawn_page(f"/Span <</ActualText (Name Acct 1234)>> BDC {_ACCOUNT} EMC"),
            "Name",
            None,
            range(10, 14),
            "AcctName",
            "null",
        ),
        # The same line small, drawn in the big one's box first: only the big line's words go.
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
        # The same number twice: the second goes, where it's drawn, and the first stays.
        (
            drawn_page("BT /F 12 Tf 72 100 Td (1234 and 1234 end) Tj ET"),
            "1234",
            None,
            range(9, 13),
            "1234andend",
            "null",
        ),
        # A title that joins the number to the word before it.
        (
            drawn_page(_ACCOUNT, title="Acct1234 Name"),
            "Acct",
            None,
            range(5, 9),
            "AcctName",
            "Acct Name",
        ),
        # A title that joins the number to the words beside it, before and after it.
        (
            drawn_page(_ACCOUNT, title="Acct1234Name and 1234Acct"),
            "Acct",
            None,
            range(5, 9),
            "AcctName",
            "AcctName and Acct",
        ),
        # A title that joins the number to a word of the line not beside it: another number.
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
        # A title that joins the number to the same word beside it: neither is left there whole.
        (
            drawn_page("BT /F 12 Tf 72 100 Td (Ref 1 - 1 Month) Tj ET", title="Plan 11"),
            "Ref",
            None,
            range(8, 9),
            "Ref1-Month",
            "Plan",
        ),
        # A range that starts inside a word: the whole word goes, from the title too.
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
        # Marked content that says other words than its glyphs: the whole line goes, and the
        # title and the marked content lose only the words.
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
    """The page loses the line's own glyphs for them, and every hidden copy the words."""
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
    """A mailto: over the line's label carries the address redacted beside it."""
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
    assert_problem(_export(mine, doc, [], pages), problem, status)


_LOWERED = 3  # each limit, lowered so a test can pass it with a short list


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
    """The longest document an upload takes: MAX_PAGES copies of one full contract page.

    Copied rather than drawn page by page, so it builds quickly; each copy is
    its own objects, as a real long file's pages are.
    """
    one_page = tmp_path_factory.mktemp("longest") / "one.pdf"
    write_dense(str(one_page), pages=1)
    longest = pymupdf.open()
    with pymupdf.open(one_page) as one:
        for _ in range(documents_constants.MAX_PAGES):
            longest.insert_pdf(one)
    return longest.tobytes()


def test_the_longest_document_exports_within_the_export_timeout(mine, longest, monkeypatch):
    """The whole way, through the API: #35 held only how a save grows with its pages."""
    monkeypatch.setattr(documents_constants, "ANALYSE_TIMEOUT_S", _UNTIMED_S)
    doc = upload(mine, longest).json()
    last_page = documents_constants.MAX_PAGES - 1
    line = span_starting(doc, last_page, _OPENING)
    edit = {"kind": "replace", "span_id": line["id"], "text": f"{_OPENING} 15 March 2026"}

    # Within EXPORT_TIMEOUT_S, or the pool stops it and the reply is too_slow.
    exported = _opened(_export(mine, doc, [edit]))

    assert_equal(exported.page_count, documents_constants.MAX_PAGES, "pages exported")
    assert_in(edit["text"], exported[last_page].get_text(), "the edit, on the last page")


_READ_ALIKE = ("Liberation Serif Regular", "Liberation Sans Regular")  # the page's two fonts
_SERIF_LINE = "Set in the serif"  # the page's first line, in Liberation Serif
_SANS_LINE = "Set in the sans"  # its second, in Liberation Sans named as Liberation Serif


def _sans_named_as_serif() -> bytes:
    """Liberation Sans, its names Liberation Serif's: text in it reads as in the serif."""
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
    """A page of two lines whose fonts read by one name: a serif's, and a sans renamed so."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="serif", fontbuffer=face_bytes(FACES["Liberation Serif Regular"]))
    page.insert_font(fontname="sans", fontbuffer=_sans_named_as_serif())
    page.insert_text((72, 96), _SERIF_LINE, fontname="serif", fontsize=12)
    page.insert_text((72, 140), _SANS_LINE, fontname="sans", fontsize=12)
    return doc.tobytes()


def test_new_text_is_drawn_in_the_font_its_object_names_where_two_read_alike(mine, read_alike):
    """Two fonts whose text reads one name: by that name, new text goes to the first.

    The page's text can't tell the two apart, so both lines read as the serif's; named by
    its object, the sans draws it.
    """
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
    """The fonts a file's first page lists, as PyMuPDF lists them, resource names and all."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return pdf[0].get_fonts()
