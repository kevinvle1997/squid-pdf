"""The path a person takes, end to end: upload, check a fix, download it.

Every other test checks one step. This one checks they add up to the promise:
a fix judged before it's made (Rule 1) comes out in the file's own font, and a
redaction is gone from the file downloaded (Rule 4).
"""

from __future__ import annotations

import base64
import math

import pymupdf

from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.core.fonts.names import strip_subset
from tests.api.conftest import around, span_starting, upload
from tests.helpers import assert_at_most, assert_equal, assert_in, assert_not_in, assert_true

_SCALE = 2


def _rect(box: dict) -> pymupdf.Rect:
    """A box from the JSON, as MuPDF takes it."""
    return pymupdf.Rect(box["x0"], box["y0"], box["x1"], box["y1"])


def _pixels(png: bytes) -> bytes:
    """An image's pixels, however it was compressed."""
    return pymupdf.Pixmap(png).samples


def _drawn_rows(page: pymupdf.Page, region: dict) -> bytes:
    """The pixels of `region`'s rows of a page, drawn as render draws a strip."""
    top = math.floor(region["y0"] * _SCALE) / _SCALE
    bottom = math.ceil(region["y1"] * _SCALE) / _SCALE
    rows = pymupdf.Rect(0, top, page.rect.width, bottom)
    return page.get_pixmap(
        matrix=pymupdf.Matrix(_SCALE, _SCALE), clip=rows, alpha=False
    ).samples


def _strip(client, doc: dict, edits: list[dict], span: dict) -> bytes:
    """The strip over `span`'s line, drawn with `edits`, as the browser asks for it."""
    body = {"edits": edits, "scale": _SCALE, "regions": [around(span)]}
    rendered = client.post(f"/api/documents/{doc['id']}/render", json=body).json()
    return base64.b64decode(rendered["images"][0]["image"])


def test_a_fix_checked_before_it_is_made_downloads_in_the_documents_own_font(
    browser, pdf_bytes
):
    mine = browser()

    # Upload: every span is judged before any edit, and each font lists what it draws.
    uploaded = upload(mine, pdf_bytes)
    assert_equal(uploaded.status_code, 201, "upload status")
    doc = uploaded.json()
    delivery, invoices = span_starting(doc, 1, "Delivery"), span_starting(doc, 1, "Invoices")
    made = span_starting(doc, 0, "Made")
    judged = (delivery["fidelity"], made["fidelity"])
    assert_equal(judged, ("exact", "substitute"), "a font in the file, and one only named")
    fonts = {font["name"]: font for font in doc["fonts"]}
    own = fonts[delivery["font"]]
    assert_in("D", own["glyphs"], "letters the trimmed font draws")
    assert_not_in("é", own["glyphs"], "letters the trimmed font draws")
    times = fonts[made["font"]]
    substitute = (times["substitute"], times["same_widths"])
    assert_equal(substitute, ("Liberation Serif Regular", True), "the named font's substitute")

    # Check: the browser draws the fix and is told it fits, before anything is saved.
    text = delivery["text"].replace("14 March", "2 March")
    replace = {"kind": "replace", "span_id": delivery["id"], "text": text}
    body = {"edits": [replace], "scale": _SCALE, "regions": []}
    fit = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()["fits"]
    fit = fit[delivery["id"]]
    assert_at_most(fit["delta_pt"], TOLERANCE_PT, "points past the original")
    assert_equal((fit["missing"], fit["message"]), ([], None), "what's wrong with it")
    preview = _strip(mine, doc, [replace], delivery)
    changed = preview != _strip(mine, doc, [], delivery)
    assert_true(changed, "the strip over the line shows the fix")

    # Download: the fix and a redaction, applied to the file itself.
    redact = {"kind": "redact", "span_id": invoices["id"]}
    body = {"edits": [replace, redact]}
    response = mine.post(f"/api/documents/{doc['id']}/export", json=body)
    kind = response.headers["content-type"]
    assert_equal((response.status_code, kind), (200, "application/pdf"), response.text[:200])
    assert_equal(response.headers["Squid-Skipped-Edits"], "", "edits left out")
    assert_equal(response.headers["Squid-Notices"], "[]", "what came out other than asked")

    pdf = pymupdf.open(stream=response.content, filetype="pdf")
    assert_equal(pdf.page_count, 2, "pages in the file")
    edited_text = pdf[1].get_text()
    assert_in(text, edited_text, "the edited page's text")
    assert_not_in("14 March", edited_text, "the edited page's text")
    everything = "".join(page.get_text() for page in pdf.pages())
    assert_not_in(invoices["text"], everything, "the file's text after the redaction")
    # Not only the whole line: nothing at all is left where it was.
    left = "".join(pdf[1].get_textbox(_rect(invoices["bbox"])).split())
    assert_equal(left, "", "letters left in the redacted line's box")
    # What you see is what exports (Rule 2): the preview is the file's own rows, exactly.
    exported = _drawn_rows(pdf[1], around(delivery))
    assert_equal(_pixels(preview), exported, "the preview strip and the exported page's rows")
    # The one number tracked: the fix kept the document's own font, as the check said.
    # A substitute would be a second font on the page; there's only the one the file had.
    on_page = {strip_subset(name) for _xref, _ext, _kind, name, *_ in pdf[1].get_fonts()}
    assert_equal(on_page, {strip_subset(delivery["font"])}, "fonts on the edited page")
