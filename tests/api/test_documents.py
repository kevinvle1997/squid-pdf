"""Upload a PDF, get every span back judged, view its pages, delete it."""

from __future__ import annotations

import os

import pymupdf
import pytest

from squidpdf.api import constants as limits
from squidpdf.core import BUILD, words
from squidpdf.core.constants import CONDENSE_LIMIT, SHRINK_FLOOR, TOLERANCE_PT
from squidpdf.documents import store
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_problem, assert_true

_A4 = {"width": 595.0, "height": 842.0, "rotation": 0}
_HUGE_PT = 3000  # a page side past the pixel limit at every scale above 1


@pytest.fixture
def mine(browser):
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine, pdf_bytes) -> dict:
    """The sample PDF as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


def _kept() -> int:
    """How many documents are on disk."""
    return len(os.listdir(store.root()))


def test_an_upload_answers_with_every_span_judged_before_any_edit(mine, pdf_bytes, engine):
    response = upload(mine, pdf_bytes)
    assert_equal(response.status_code, 201, "upload status")
    got = {s["id"]: s["fidelity"] for s in response.json()["spans"]}
    expected = {r.span_id: r.state.value for r in engine.assess(engine.index())}
    assert_equal(got, expected, "each span's fidelity, against the engine's own")


def test_every_font_lists_only_the_glyphs_it_really_draws(doc):
    fonts = {f["name"]: f for f in doc["fonts"]}
    for span in doc["spans"]:
        assert_in(span["font"], fonts, "fonts the document lists")
    embedded = fonts[next(s["font"] for s in doc["spans"] if s["fidelity"] == "exact")]
    assert_in("D", embedded["glyphs"], "glyphs of the subset font")
    assert_not_in("é", embedded["glyphs"], "glyphs of the subset font")


def test_the_document_brings_its_pages_fit_rules_and_sentences(doc):
    assert_equal(doc["build"], BUILD, "build")
    assert_equal(doc["pages"], [_A4, _A4], "pages")
    rules = {
        "tolerance_pt": TOLERANCE_PT,
        "condense_limit": CONDENSE_LIMIT,
        "shrink_floor": SHRINK_FLOOR,
    }
    assert_equal(doc["fit"], rules, "fit")
    assert_equal(doc["copy"]["missing"], words.MISSING, "the missing-glyph sentence")
    assert_equal(doc["notices"], [], "notices")


def test_a_page_is_a_png_the_browser_keeps_for_an_hour(mine, doc):
    response = mine.get(
        f"/api/documents/{doc['id']}/pages/0", params={"scale": 2, "build": doc["build"]}
    )
    assert_equal(response.headers["content-type"], "image/png", "page media type")
    assert_equal(
        response.headers["cache-control"], "private, max-age=3600, immutable", "page caching"
    )
    pix = pymupdf.Pixmap(response.content)
    assert_equal((pix.width, pix.height), (595 * 2, 842 * 2), "pixels at scale 2")


def test_a_page_asked_for_under_an_old_build_is_not_kept(mine, doc):
    params = {"scale": 1, "build": "an-older-build"}
    response = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params)
    assert_equal(response.headers["cache-control"], "no-store", "caching under an old build")


def test_a_page_past_the_pixel_limit_gets_a_smaller_scale(mine):
    big = pymupdf.open()
    big.new_page(width=_HUGE_PT, height=_HUGE_PT)
    doc = upload(mine, big.tobytes()).json()
    params = {"scale": max(limits.PAGE_SCALES), "build": doc["build"]}
    png = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params).content
    pix = pymupdf.Pixmap(png)
    assert_true(
        pix.width * pix.height <= limits.MAX_IMAGE_PIXELS,
        f"{pix.width}x{pix.height} is past the pixel limit",
    )


def test_reading_it_again_unchanged_answers_not_modified(mine, doc):
    first = mine.get(f"/api/documents/{doc['id']}")
    assert_equal(first.headers["cache-control"], "private, no-cache", "document caching")
    again = mine.get(
        f"/api/documents/{doc['id']}", headers={"if-none-match": first.headers["etag"]}
    )
    assert_equal(again.status_code, 304, "status for an unchanged document")


def test_deleting_it_leaves_nothing_behind(mine, doc):
    before = _kept()
    assert_equal(mine.delete(f"/api/documents/{doc['id']}").status_code, 204, "delete status")
    assert_problem(mine.get(f"/api/documents/{doc['id']}"), "not_found", 404)
    assert_equal(_kept(), before - 1, "documents on disk after a delete")


@pytest.mark.parametrize(
    ("body", "problem", "status"),
    [
        (b"Dear Sir, please find attached.", "not_a_pdf", 415),
        # A PNG's opening bytes, then zeros.
        (b"\x89PNG\r\n\x1a\n" + bytes(4096), "not_a_pdf", 415),
        # Starts like a PDF, then every byte value over and over: no PDF inside.
        (b"%PDF-1.7\n" + bytes(range(256)) * 8, "damaged", 422),
    ],
    ids=["text", "png", "garbage after the header"],
)
def test_a_file_that_wont_open_is_refused_and_nothing_kept(mine, body, problem, status):
    before = _kept()
    assert_problem(upload(mine, body), problem, status)
    assert_equal(_kept(), before, "documents on disk after a refusal")


def test_a_file_over_the_limit_is_refused_while_it_streams(mine, pdf_bytes, monkeypatch):
    monkeypatch.setattr(limits, "MAX_FILE_BYTES", len(pdf_bytes) // 2)
    before = _kept()
    chunks = iter([pdf_bytes[:1024], pdf_bytes[1024:]])  # no length up front: it streams
    response = mine.post("/api/documents", content=chunks)
    assert_problem(response, "too_large", 413)
    assert_equal(response.json()["detail"], "This file is over 100 MB.", "the refusal")
    assert_equal(_kept(), before, "documents on disk after a refusal")
