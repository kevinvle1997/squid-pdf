"""The real app with its workers running, one for the whole run, and browsers to call it."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from squidpdf.api import constants as api_constants
from squidpdf.api.app import create_app

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"
_MARGIN_PT = 4  # above and below a line, as the browser pads its strip


def _crashes() -> APIRouter:
    """A route with a bug in it, to see what a crash looks like from outside."""
    router = APIRouter()

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    return router


# Every test uploads from one address, far more often than a browser may: plenty for the suite.
_SUITE_UPLOADS_PER_MINUTE = 10_000


@pytest.fixture(scope="session")
def session_app(tmp_path_factory) -> Iterator[FastAPI]:
    """One app for the whole run, workers started once: a pool's first task costs a second.

    Under xdist each worker is its own session, so each has one. A test that kills a
    worker or changes the app for good builds its own pool or app, never this one.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        # The per-address upload limit reads this when each upload comes in.
        env.setattr(api_constants, "UPLOADS_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        app = create_app()
        app.include_router(_crashes())
        yield app


@pytest.fixture(scope="session")
def server(session_app: FastAPI) -> Iterator[TestClient]:
    """The run's app started, its pool and sweeper with it, in the loop every browser shares."""
    with TestClient(session_app) as server:  # runs the lifespan
        yield server


@pytest.fixture(scope="module")
def app(session_app: FastAPI, server: TestClient, tmp_path_factory) -> Iterator[FastAPI]:
    """The run's app, started, with this module's documents kept in a fresh folder of their own.

    The folder is read on each request, and work goes to the pool by absolute path,
    so the workers started for an earlier module find this one's documents.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        yield session_app


def browser_on(server: TestClient, **options: Any) -> TestClient:
    """A new browser on `server`'s app, with cookies of its own; `options` go to TestClient.

    Its requests run in the loop `server` started the app in, as a server runs
    one: the pool works only in the loop it was made in, and a TestClient on its
    own gives each request a new loop.
    """
    browser = TestClient(server.app, base_url=BASE_URL, **options)
    browser.portal = server.portal
    return browser


@pytest.fixture
def browser(app: FastAPI, server: TestClient) -> Callable[[], TestClient]:
    """Opens a new browser on the app each call; each keeps its own cookies."""
    return lambda: browser_on(server)


def upload(client: TestClient, body: bytes) -> Response:
    """Send a file the way the browser does: the raw bytes, no form."""
    return client.post(
        "/api/documents", content=body, headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def pdf_bytes(pdf: str) -> bytes:
    """The shared sample PDF, as a browser would upload it."""
    return Path(pdf).read_bytes()


@pytest.fixture
def mine(browser: Callable[[], TestClient]) -> TestClient:
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine: TestClient, pdf_bytes: bytes) -> dict:
    """The sample PDF as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


def span_starting(doc: dict, page: int, starts: str) -> dict:
    """The span on `page` of an uploaded `doc` whose text starts with `starts`."""
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def around(span: dict) -> dict:
    """A region: the full-width strip over a span's line."""
    box = span["bbox"]
    return {"page": span["page"], "y0": box["y0"] - _MARGIN_PT, "y1": box["y1"] + _MARGIN_PT}


# The facts of `with_hidden_copies`: the name its page shows, redacted, and the words of
# their own the hidden copies hold around it, which stay.
REDACTED_NAME = "Ada Quill"
OTHER_LINE = "Other line"  # the page's other line, which nothing redacts
CHOSEN = "Bob Smith"  # the choice fields' other option, the one they show
# Every copy of the name `with_hidden_copies` can make at once.
NAME_COPIES = (
    "title",
    "author",
    "xmp",
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
    "xfa",
    "tag",
)
# The copies of the name in metadata that isn't read as XML, each made alone: a file keeps
# one metadata stream.
NOT_XML_COPIES = ("not_xml", "not_xml_utf16", "xmp_utf32", "xmp_doctype", "xmp_deep")
# The ways `with_hidden_copies` signs its page: a signed field, one that certifies the
# document, and one kept checkable for years (LTV).
SIGNED = ("signature", "certified", "dss")
# PyMuPDF's PDF_WIDGET_TYPE_TEXT, _LISTBOX, _COMBOBOX, _SIGNATURE and _BUTTON, set at import,
# so type checkers can't see them
_TEXT_FIELD = 7
_LIST_FIELD = 4
_CHOICE_FIELD = 3
_SIGNATURE_FIELD = 6
_BUTTON_FIELD = 1
_MULTI_SELECT = 1 << 21  # a list box's flag (Ff) that lets it show more than one option
# Metadata (XMP), as Acrobat writes it: the name as the author (a list of one), and in the
# description with words of its own. A note to a program (a processing instruction), which
# XML allows anywhere, also names them.
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
# Metadata whose DOCTYPE declares the name as a word of its own (an entity), which its text
# holds only spelled out.
_DECLARING_XMP = (
    '<!DOCTYPE x [<!ENTITY who "{name}">]>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:creator>&who;</dc:creator></x:xmpmeta>"
)
# The room a signature leaves for its signer's certificates and its value, zeros here:
# nothing reads them.
_SIGNED_BYTES = bytes(64)
# How deep metadata nests that's nested deeper than any program writes it: far past what
# Python reads by calls.
_DEEP = 3000
# The form again, as XFA's data (its datasets), as a form made for both kinds of viewer has it.
_XFA_DATA = (
    '<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/">'
    "<xfa:data><form><who>{name}</who></form></xfa:data></xfa:datasets>"
)
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

    The page's top line is the name; its other line, `OTHER_LINE`, nothing
    redacts. The copies, as a file names a person: its title, "Notes on" the
    name, and its author, the name alone (its Info); its metadata (XMP), the
    name as the author, in the description and in a note to a program; two
    bookmarks, "Visit by" the name, and the name alone; a sticky note saying
    "Call" the name, written by the name, on a "Visit", its words also
    formatted (RC); a comment written on the page below the lines (a
    FreeText), "Ask" the name; another, whose words say "Ring" but whose
    drawing still says "Ring" the name, as a file edited without drawing it
    again leaves one; a text field showing the name, which also resets to
    it (DV) and keeps it formatted (RV), as a rich text field does; a text
    field showing the name that names no kind (FT), so MuPDF can't draw it
    again, though a viewer draws the old drawing; a choice field showing
    `CHOSEN`, which offers the name too, as a pair: what it saves ("2") and
    what it shows; a list box showing both, its value a list of the two; a
    text field showing the name that the form doesn't list, as a
    half-flattened form leaves one; an empty text field described "Signature
    of" the name (TU), which a screen reader reads for it; another, its part
    on the page described "Phone of" the name (the widget's Contents); a
    signature field not signed yet whose seed values (SV) say the name must
    sign it; a push button captioned "Email" the
    name; a stamp whose drawing writes "Seen by" the name only as a string
    in hex, so its strings must be read one by one; the form's data again
    as XFA; and the tags, a picture's description (Alt), "Photo of" the
    name. The comments and the fields sit apart from the lines, so erasing
    the name's letters can't reach them.

    Each of `NOT_XML_COPIES`, made alone, is metadata holding the name that
    isn't read as XML: not XML, in UTF-8 or UTF-16; XMP in UTF-32; XMP whose
    DOCTYPE declares the name; or XMP nested deeper than metadata is read.
    Each of `SIGNED` signs the page, holding no word of it: a signature
    field signed; another, which certifies the document, so what the
    document permits (Perms) names it too; or a third, with what the file
    keeps to check signatures for years (DSS), a certificate, again for that
    signature (VRI).
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
        doc.xref_set_key(stale.xref, "Contents", "(Ring)")
    if "field" in copies:
        field = _add_field(page, "who", REDACTED_NAME, top=400)
        doc.xref_set_key(field, "DV", f"({REDACTED_NAME})")
        doc.xref_set_key(field, "RV", f"(<body><p>{REDACTED_NAME}</p></body>)")
    if "undrawable_field" in copies:
        undrawable = _add_field(page, "sealed", REDACTED_NAME, top=400, left=320)
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
    if "xfa" in copies:
        data = _new_stream(doc, _XFA_DATA.format(name=REDACTED_NAME).encode())
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/XFA", f"[(datasets) {data} 0 R]")
    if "tag" in copies:
        _tag_a_picture(doc, f"Photo of {REDACTED_NAME}")
    return doc.tobytes()


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


def _stamp(doc: pymupdf.Document, page: pymupdf.Page, words: str) -> None:
    """Add a stamp whose drawing writes `words` as a string in hex, its letters' codes."""
    stamp = page.add_stamp_annot(pymupdf.Rect(320, 610, 548, 650), stamp=0)
    _kind, drawing = doc.xref_get_key(stamp.xref, "AP/N")
    drawn = f"BT /Helv 12 Tf 2 5 Td <{words.encode().hex()}> Tj ET".encode()
    doc.update_stream(int(drawing.split()[0]), drawn)


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
