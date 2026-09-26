"""Send edits and the rows on screen; get those rows drawn, a fit per replace, and the skips."""

from __future__ import annotations

import base64

import pymupdf
import pytest

from squidpdf.core import words
from squidpdf.core.constants import TOLERANCE_PT
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_problem, assert_true

_SCALE = 2
_MARGIN_PT = 4  # above and below a line, as the browser pads its strip
_LONGER = "!!"  # a few points too long: every way out is offered
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale


@pytest.fixture
def mine(browser):
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine, pdf_bytes) -> dict:
    """The sample PDF as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


def _span(doc: dict, page: int, starts: str) -> dict:
    """The span on `page` whose text starts with `starts`."""
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def _around(span: dict) -> dict:
    """A region: the full-width strip over a span's line."""
    box = span["bbox"]
    return {"page": span["page"], "y0": box["y0"] - _MARGIN_PT, "y1": box["y1"] + _MARGIN_PT}


def _render(client, doc: dict, edits: list[dict], regions: list[dict]):
    """Ask for `regions` drawn with `edits`, as the browser does."""
    body = {"edits": edits, "scale": _SCALE, "regions": regions}
    return client.post(f"/api/documents/{doc['id']}/render", json=body)


def _png(image: dict) -> bytes:
    """One of render's images, decoded."""
    return base64.b64decode(image["image"])


def test_a_replace_comes_back_drawn_with_a_fit_that_says_it_fits(mine, doc):
    span = _span(doc, 1, "Delivery")
    text = span["text"].replace("14 March", "2 March")
    edit = {"kind": "replace", "span_id": span["id"], "text": text}

    edited = _render(mine, doc, [edit], [_around(span)]).json()
    original = _render(mine, doc, [], [_around(span)]).json()

    assert_true(_png(edited["images"][0]) != _png(original["images"][0]), "the strip changed")
    fit = edited["fits"][span["id"]]
    assert_true(fit["delta_pt"] <= TOLERANCE_PT, f"{fit['delta_pt']} pt past the original")
    assert_equal((fit["missing"], fit["message"]), ([], None), "what's wrong with it")
    assert_equal(edited["skipped"], [], "skipped")


def test_a_replace_too_long_says_by_how_much_and_offers_the_ways_out(mine, doc):
    span = _span(doc, 0, "Made")
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"] + _LONGER}

    fit = _render(mine, doc, [edit], []).json()["fits"][span["id"]]

    assert_true(fit["delta_pt"] > TOLERANCE_PT, f"{fit['delta_pt']} pt past the original")
    assert_equal(fit["options"], ["shrink", "condense", "as-is"], "the ways out")
    too_long = words.TOO_LONG.format(delta_pt=f"{fit['delta_pt']:.1f}")
    assert_equal(fit["message"], too_long, "the message")


def test_rows_with_no_edits_are_the_page_image_exactly(mine, doc):
    """Else a strip laid over the page image would show a seam."""
    edited = _span(doc, 1, "Delivery")
    edit = {"kind": "replace", "span_id": edited["id"], "text": "Delivery begins 2 March"}
    strip = {"page": 0, "y0": _OFF_GRID_PT, "y1": _OFF_GRID_PT + 60}

    rendered = _render(mine, doc, [edit], [strip, {"page": 0}]).json()["images"]
    # One image per region, in the order asked.
    strip_image, whole_page = rendered[0], rendered[1]

    params = {"scale": _SCALE, "build": doc["build"]}
    page_png = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params).content
    assert_equal(_png(whole_page), page_png, "the whole page, against the page image")
    page, rows = pymupdf.Pixmap(page_png), pymupdf.Pixmap(_png(strip_image))
    # The page image's pixel rows under the strip; stride is the bytes in one row.
    top = round(strip_image["y"] * _SCALE)
    expected = page.samples[top * page.stride : (top + rows.height) * page.stride]
    assert_equal(rows.width, page.width, "strip width")
    assert_true(rows.samples == expected, f"the strip at y={strip_image['y']} matches its rows")


def test_an_edit_pointing_at_nothing_is_skipped_and_named(mine, doc):
    span = _span(doc, 0, "Made")
    edits = [
        {"kind": "replace", "span_id": "nosuchspan00", "text": "x"},
        {"kind": "replace", "span_id": span["id"], "text": "Made on 2 April 2026."},
    ]

    rendered = _render(mine, doc, edits, [_around(span)]).json()

    expected = [{"edit": 0, "type": "bad_reference", "detail": words.NO_SPAN}]
    assert_equal(rendered["skipped"], expected, "skipped")
    assert_not_in("nosuchspan00", rendered["fits"], "fits")


def test_a_redaction_pointing_at_nothing_fails_the_whole_request(mine, doc):
    """Skipping it would leave the text the user asked to remove."""
    edits = [{"kind": "redact", "span_id": "nosuchspan00"}]
    response = _render(mine, doc, edits, [{"page": 0}])
    assert_problem(response, "bad_reference", 422)
    assert_in("nosuchspan00", response.json()["detail"], "the detail naming the span")


def test_another_browser_is_told_there_is_no_such_document(browser, doc, mine):
    assert_equal(_render(mine, doc, [], [{"page": 0}]).status_code, 200, "the owner's status")
    assert_problem(_render(browser(), doc, [], [{"page": 0}]), "not_found", 404)
