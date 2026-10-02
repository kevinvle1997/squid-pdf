"""Send edits and the rows on screen; get those rows drawn, a fit per replace, and the skips."""

from __future__ import annotations

import base64
import json

import pymupdf
import pytest

from squidpdf.core import Engine, words
from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.documents import store
from tests.api.conftest import around, span_starting
from tests.helpers import assert_equal, assert_in, assert_problem, assert_true

_SCALE = 2
_LONGER = "!!"  # a few points too long: every way out is offered
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale
_INSERT = {"kind": "insert", "page": 0, "origin": [72, 700], "text": "Signed", "size": 12}


class _InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run(self, _timeout, task):
        return task()


def _render(client, doc: dict, edits: list[dict], regions: list[dict]):
    """Ask for `regions` drawn with `edits`, as the browser does."""
    body = {"edits": edits, "scale": _SCALE, "regions": regions}
    return client.post(f"/api/documents/{doc['id']}/render", json=body)


def _png(image: dict) -> bytes:
    """One of render's images, decoded."""
    return base64.b64decode(image["image"])


def test_a_replace_too_long_says_by_how_much_and_offers_the_ways_out(mine, doc):
    span = span_starting(doc, 0, "Made")
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"] + _LONGER}

    fit = _render(mine, doc, [edit], []).json()["fits"][span["id"]]

    assert_true(fit["delta_pt"] > TOLERANCE_PT, f"{fit['delta_pt']} pt past the original")
    assert_equal(fit["options"], ["shrink", "condense", "as-is"], "the ways out")
    too_long = words.sentence("too_long").format(delta_pt=f"{fit['delta_pt']:.1f}")
    assert_equal(fit["message"], too_long, "the message")


def test_rows_with_no_edits_are_the_page_image_exactly(mine, doc):
    """Else a strip laid over the page image would show a seam."""
    delivery = span_starting(doc, 1, "Delivery")
    edit = {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"}
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


@pytest.mark.parametrize("kind", ["replace", "insert"])
@pytest.mark.parametrize(
    "character",
    ["\n", "\r", "\t", "\ud800"],
    ids=["line break", "carriage return", "tab", "half an emoji"],
)
def test_new_text_that_isnt_one_line_of_letters_is_a_bad_request(mine, doc, kind, character):
    """A line break would draw a second line over the next; half an emoji draws nothing."""
    span = span_starting(doc, 0, "Made")
    replace = {"kind": "replace", "span_id": span["id"], "text": f"Made{character}on"}
    insert = {**_INSERT, "text": f"Sig{character}ned"}
    edit = replace if kind == "replace" else insert
    body = {"edits": [edit], "scale": _SCALE, "regions": [{"page": 0}]}

    # Escaped, as a browser's JSON.stringify sends half an emoji.
    response = mine.post(
        f"/api/documents/{doc['id']}/render",
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )

    assert_problem(response, "invalid_request", 400)
    assert_in("text", response.json()["debug"], "what a developer reads")


def test_an_edit_that_doesnt_say_its_kind_is_a_bad_request(mine, doc):
    """Never guessed as a replace: `kind` is what tells the edits apart."""
    span = span_starting(doc, 0, "Made")

    response = _render(mine, doc, [{"span_id": span["id"], "text": "x"}], [{"page": 0}])

    assert_problem(response, "invalid_request", 400)
    assert_in("kind", response.json()["debug"], "what a developer reads")


@pytest.mark.parametrize(
    "region",
    [{"page": 0, "y0": 900, "y1": 1000}, {"page": 0, "y0": -100, "y1": -50}],
    ids=["below the page", "above it"],
)
def test_a_strip_off_the_page_is_a_bad_request(mine, doc, region):
    """Nothing to draw there: the browser asked for rows the page doesn't have."""
    assert_problem(_render(mine, doc, [], [region]), "invalid_request", 400)


def test_only_the_edits_in_the_rows_asked_for_are_redrawn(app, mine, doc, monkeypatch):
    """A strip over one line of a page with others edited: redrawing them all was most of it.

    Every replace still gets its fit: that's measurement, not drawing.
    """
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patch below reaches it
    redrawn: list[str] = []
    draw = Engine.draw

    def noting_what(engine: Engine, span, text: str, **setting) -> list:
        redrawn.append(text)
        return draw(engine, span, text, **setting)

    monkeypatch.setattr(Engine, "draw", noting_what)
    delivery, invoices = span_starting(doc, 1, "Delivery"), span_starting(doc, 1, "Invoices")
    edits = [
        {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"},
        {"kind": "replace", "span_id": invoices["id"], "text": "Invoices are due at once."},
    ]
    fits = _render(mine, doc, edits, [around(invoices)]).json()["fits"]

    assert_equal(redrawn, ["Invoices are due at once."], "the lines redrawn")
    assert_equal(set(fits), {delivery["id"], invoices["id"]}, "the replaces with a fit")


def test_render_reads_the_page_list_once(app, mine, doc, monkeypatch):
    """The request reads it to check the regions, and hands the work the pages it draws."""
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the worker's reads are counted
    reads: list[object] = []
    load_pages = store.load_pages

    def counted(folder, *args, **kwargs) -> list:
        reads.append(folder)
        return load_pages(folder, *args, **kwargs)

    monkeypatch.setattr(store, "load_pages", counted)

    response = _render(mine, doc, [], [{"page": 0}])

    assert_equal(response.status_code, 200, "render status")
    assert_equal(len(reads), 1, "times the page list was read")
