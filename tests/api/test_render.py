"""Send edits and the rows on screen; get those rows drawn, a fit per replace, and the skips."""

from __future__ import annotations

import base64

import pymupdf
import pytest

from squidpdf.core import words
from squidpdf.core.constants import TOLERANCE_PT
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_problem, assert_true

_SCALE = 2
_LONGER = "!!"  # a few points too long: every way out is offered
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale
_INSERT = {"kind": "insert", "page": 0, "origin": [72, 700], "text": "Signed", "size": 12}


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


def _render(client, doc: dict, edits: list[dict], regions: list[dict]):
    """Ask for `regions` drawn with `edits`, as the browser does."""
    body = {"edits": edits, "scale": _SCALE, "regions": regions}
    return client.post(f"/api/documents/{doc['id']}/render", json=body)


def _png(image: dict) -> bytes:
    """One of render's images, decoded."""
    return base64.b64decode(image["image"])


def test_a_replace_too_long_says_by_how_much_and_offers_the_ways_out(mine, doc):
    span = _span(doc, 0, "Made")
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"] + _LONGER}

    fit = _render(mine, doc, [edit], []).json()["fits"][span["id"]]

    assert_true(fit["delta_pt"] > TOLERANCE_PT, f"{fit['delta_pt']} pt past the original")
    assert_equal(fit["options"], ["shrink", "condense", "as-is"], "the ways out")
    too_long = words.sentence("too_long").format(delta_pt=f"{fit['delta_pt']:.1f}")
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


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"size": 0}, "size"),
        ({"color": [5, 0, 0]}, "color"),
    ],
    ids=["size 0", "a color past 1"],
)
def test_an_insert_nothing_can_draw_is_a_bad_request(mine, doc, change, field):
    """Not drawn mirrored, not a crash in the worker: the browser sent something wrong."""
    response = _render(mine, doc, [{**_INSERT, **change}], [{"page": 0}])
    assert_problem(response, "invalid_request", 400)
    assert_in(field, response.json()["debug"], "what a developer reads")
