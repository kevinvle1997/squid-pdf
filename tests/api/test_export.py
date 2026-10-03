"""Send edits, get the file back: every edit applied, and each redaction checked on it."""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Collection, Iterator
from pathlib import Path
from unittest import mock

import pymupdf
import pytest
from fastapi.testclient import TestClient
from fontTools.subset import Subsetter

from squidpdf.api.pool import WorkerPool, start_pool
from squidpdf.core import Engine, words, write_dense
from squidpdf.documents import constants as documents_constants, store
from squidpdf.editing import Edit, constants as editing_constants, export
from squidpdf.editing.types import Exported
from tests.api.conftest import (
    CHOSEN,
    NAME_COPIES,
    NOT_XML_COPIES,
    OTHER_LINE,
    REDACTED_NAME,
    SIGNED,
    InProcess,
    around,
    span_starting,
    upload,
    with_hidden_copies,
)
from tests.conftest import cannot_cut
from tests.helpers import (
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
)
# A hidden copy as a file writes it: the key, then the text in brackets.
_HIDDEN_COPY = re.compile(r"/(ActualText|Alt|E)\s*\(([^)]*)\)")


_STALL_S = 60  # far past any export timeout here
_STALLED_TIMEOUT_S = 3  # long enough to start the export, short enough to wait for
_make_pdf = export._make_pdf  # the real one, kept before a test swaps it


def _stall(*_args: object, **_kwargs: object) -> None:
    """Hangs, as MuPDF can on a hostile file."""
    time.sleep(_STALL_S)


def _make_pdf_stalling_once_open(
    folder: str, scratch: str, *, edits: list[Edit], pages: list[int] | None
) -> Exported:
    """`_make_pdf`, hanging where it opens the original. Runs in a worker, the patch with it."""
    with mock.patch.object(store, "open_original", _stall):
        return _make_pdf(folder, scratch, edits=edits, pages=pages)


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
    in the file, and the check reads it where its page went.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "remove", lambda _engine, _spans, then_drawn: [])
    span = span_starting(doc, 1, "Invoices")
    kept = _files(doc)

    response = _export(mine, doc, [_redact(span)], pages=pages)

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
    drawing.append(f"BT /Lig 12 Tf 72 600 Td (A US card on \\001le: ) Tj {number} ET")
    contents = _new_stream(doc, "\n".join(drawing))
    doc.xref_set_key(page.xref, "Contents", f"{contents} 0 R")
    return doc.tobytes()


def _new_stream(doc: pymupdf.Document, drawing: str, **keys: str) -> int:
    """A new stream in `doc` holding `drawing`, with these keys; its number."""
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, drawing.encode())
    for key, value in keys.items():
        doc.xref_set_key(xref, key, value)
    return xref


def _everything_in(pdf: pymupdf.Document) -> str:
    """Every object in the file and every stream, decoded, as one text."""
    return " ".join(
        f"{pdf.xref_object(xref)} {pdf.xref_stream(xref) or b''!r}"
        for xref in range(1, pdf.xref_length())
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
    ],
)
def test_a_hidden_copy_still_in_the_saved_file_downloads_nothing(app, mine, monkeypatch, place):
    """The letters are gone, but a hidden copy is as much a leak: no file, the span named.

    One place at a time, so the check is shown to read each place on its own:
    a hidden copy with a NUL before the line is read past it, a string of its
    own included; one whose dictionary is written before its tag; one under
    the first of a key written twice, which MuPDF doesn't keep; a dictionary
    not read whole, which the removal leaves as written, whether MuPDF stops
    reading it after the line or before, at a key that's a string or at an ID,
    or reads on past a >> inside an array where a count stops, or both stop
    before the line; and all that follows an image MuPDF can't read, which
    the removal leaves too. What follows either is read from wherever a
    string may start: after a byte of an image that MuPDF's reader of a
    drawing takes as starting a comment, a string or a string in hex, which
    a reader that ends the image at its first EI doesn't.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "drop_hidden_copies", lambda _engine, _spans: None)
    doc = upload(mine, _marked_pdf([place])).json()
    card = span_starting(doc, 0, "A US card")

    response = _export(mine, doc, [_redact(card)])

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=_CARD_LINE, page=1)
    assert_equal(response.json()["detail"], said, "what the user reads")


def test_a_hidden_copy_the_redaction_cannot_rewrite_is_warned_at_render_and_refused_at_export(
    mine,
):
    """The user hears it while they can still undo it, and the words never leave in a file.

    A dictionary MuPDF can't read holds the line in what a screen reader reads, so the
    redaction can't rewrite it.
    """
    doc = upload(mine, _marked_pdf(["unreadable"])).json()
    card = span_starting(doc, 0, "A US card")
    edits = [_redact(card)]
    body = {"edits": edits, "scale": 1, "regions": [around(card)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    exported = _export(mine, doc, edits)

    said = words.sentence("not_redacted")
    expected = {"verified": False, "hidden_copies": ["screen_reader_text"], "message": said}
    assert_equal(rendered["redactions"], {card["id"]: expected}, "what render says of it")
    notices = [(notice["span_id"], notice["code"]) for notice in rendered["notices"]]
    assert_equal(notices, [(card["id"], "not_redacted")], "render's notices")
    assert_problem(exported, "redaction_failed", 422)


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
        "xfa": ("null", "null"),
        "tag": ("string", "Photo of"),
    }
    assert_equal(_kept_in(saved), expected, "what each place holds")
    assert_not_in("Quill", _everything_in(saved), "the name, anywhere in the file")
    # MuPDF draws the stamp, its drawing gone, and the unsigned signature field its own way.
    expected_lines = [[OTHER_LINE, "Ask", "Ring", "APPROVED", CHOSEN, CHOSEN, "SIGN", "Email"]]
    assert_equal(
        _lines(saved), expected_lines, "the page's lines, the comments' and the fields'"
    )


@pytest.mark.parametrize("copy", [*NAME_COPIES, *NOT_XML_COPIES])
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
    assert_equal(rendered["redactions"], {name["id"]: told}, "what render says of it")
    expected = (["null"], "null", "null")
    assert_equal(_signatures_in(_opened(response)), expected, "the signatures, Perms and DSS")


def test_a_signed_document_exported_with_no_redaction_keeps_its_signatures(mine):
    """Only a redaction takes them away: a replace leaves each as the export writes it."""
    doc = upload(mine, with_hidden_copies(SIGNED)).json()
    line = span_starting(doc, 0, OTHER_LINE)
    edits = [{"kind": "replace", "span_id": line["id"], "text": "Other lane"}]

    response = _export(mine, doc, edits)

    expected = (["xref"] * 3, "dict", "dict")
    assert_equal(_signatures_in(_opened(response)), expected, "the signatures, Perms and DSS")


def _signatures_in(pdf: pymupdf.Document) -> tuple[list[str], str, str]:
    """What kind each signature field's value is, then the catalog's Perms and DSS.

    A signature reads "xref"; a key the file leaves out, "null".
    """
    catalog = pdf.pdf_catalog()
    values = [pdf.xref_get_key(field.xref, "V")[0] for field in pdf[0].widgets()]
    return values, pdf.xref_get_key(catalog, "Perms")[0], pdf.xref_get_key(catalog, "DSS")[0]


# Metadata (XMP) as Acrobat writes it: in its packet's wrapper, with typed values of each kind.
_TYPED_XMP = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    ' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:dc="http://purl.org/dc/elements/1.1/"'
    ' xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"'
    ' xmlns:xmpTPg="http://ns.adobe.com/xap/1.0/t/pg/"'
    ' xmp:ModifyDate="2024-05-03T10:00:00Z" xmpMM:DocumentID="xmp.did:3F2A9C"'
    ' pdf:Trapped="False" pdfaid:part="2" pdfaid:conformance="B">'
    "<xmp:CreateDate>2024-05-03T10:00:00Z</xmp:CreateDate>"
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
    """
    doc = upload(mine, typed_metadata).json()
    line = span_starting(doc, 0, "Begin")
    body = {"edits": [_redact(line)], "scale": 1, "regions": [around(line)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    response = _export(mine, doc, [_redact(line)])

    places = rendered["redactions"][line["id"]]["hidden_copies"]
    assert_equal(places, [], "the places render names")
    assert_equal(_opened(response).get_xml_metadata(), _TYPED_XMP, "the metadata")


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
        "xfa": pdf.xref_get_key(catalog, "AcroForm/XFA"),
        "tag": pdf.xref_get_key(catalog, "StructTreeRoot/K/Alt"),
    }


def test_a_document_deleted_while_its_export_runs_still_downloads(app, mine, doc, monkeypatch):
    """Its owner's DELETE, or the sweep, takes the folder once the original is open.

    The export used to save into that folder, so the save failed and the
    browser was told the file needs more memory.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    opened = store.open_original

    def deleted_once_open(folder: Path, *args, **kwargs) -> Engine:
        engine = opened(folder, *args, **kwargs)
        store.delete(folder)
        return engine

    monkeypatch.setattr(store, "open_original", deleted_once_open)
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
    monkeypatch.setattr(export, "_make_pdf", _make_pdf_stalling_once_open)
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


def test_an_edit_after_a_redaction_on_the_same_text_fails_the_export(mine, doc):
    """Redaction wins: the list's order must not bring the text back into the file."""
    span = next(s for s in doc["spans"] if s["page"] == 1)
    redact = {"kind": "redact", "span_id": span["id"]}
    replace = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [redact, replace])

    assert_problem(response, "redaction_conflict", 422)


def test_a_redaction_pointing_at_nothing_fails_the_export(mine, doc):
    """Skipping it would send the text the user asked to remove."""
    response = _export(mine, doc, [{"kind": "redact", "span_id": "nosuchspan00"}])
    assert_problem(response, "bad_reference", 422)
    got = response.json()
    expected = ("bad_reference", {"span_id": "nosuchspan00"})
    assert_equal((got["code"], got["params"]), expected, "the problem, unsaid")


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
