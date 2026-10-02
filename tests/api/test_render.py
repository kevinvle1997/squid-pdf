"""Send edits and the rows on screen; get those rows drawn, a fit per replace, and the skips."""

from __future__ import annotations

import base64
import itertools
import json

import pymupdf
import pytest

from squidpdf.api import constants as api_constants
from squidpdf.core import COPY_PLACES, Engine, Message, words, write_dense
from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.documents import store
from squidpdf.editing import constants as editing_constants
from tests.api.conftest import (
    NAME_COPIES,
    REDACTED_NAME,
    around,
    span_starting,
    upload,
    with_hidden_copies,
)
from tests.helpers import assert_equal, assert_in, assert_problem, assert_true

_SCALE = 2
_LONGER = "!!"  # a few points too long: every way out is offered
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale
_LOWERED = 3  # the region limit, lowered so a test can pass it with a short list
_WIDEST_PT = 14_400  # the longest side a PDF's page can have
_SHORT_PT = 72  # an inch: cut into the region limit's slivers, each far under a row
_STACKED = 4  # the long contract's pages on one tall page
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


# What render says of a redaction whose words a signed file kept in every place.
_EVERY_PLACE_SAID = words.render_all(
    [
        Message(
            "hidden_copies",
            {"places": [words.sentence(f"place_{place}") for place in COPY_PLACES]},
        ),
        Message("signatures_removed"),
    ]
)


@pytest.mark.parametrize(
    ("copies", "named", "said"),
    [
        ([*NAME_COPIES, "signature"], [*COPY_PLACES, "signatures"], _EVERY_PLACE_SAID),
        ((), [], None),
    ],
    ids=["kept in every place, and signed", "kept nowhere else"],
)
def test_a_redaction_drawn_names_each_place_the_file_also_kept_its_words(
    mine, copies, named, said
):
    """Said before the download: a title isn't on the page. Each place once, then signatures."""
    doc = upload(mine, with_hidden_copies(copies)).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    rendered = _render(mine, doc, [{"kind": "redact", "span_id": name["id"]}], [around(name)])

    expected = {"verified": True, "hidden_copies": named, "message": said}
    assert_equal(rendered.json()["redactions"], {name["id"]: expected}, "what render says")


def test_every_place_a_file_keeps_a_hidden_copy_has_its_word():
    """Each place is said in the reader's words, by `place_<it>`."""
    for place in COPY_PLACES:
        assert_in(f"place_{place}", words.CATALOGS[words.ENGLISH], "the places named")


def test_rows_with_no_edits_are_the_page_image_exactly(mine, doc):
    """Else a strip laid over the page image would show a seam."""
    delivery = span_starting(doc, 1, "Delivery")
    edit = {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"}
    strip = {"page": 0, "y0": _OFF_GRID_PT, "y1": _OFF_GRID_PT + 60}

    # Asked apart: one request never asks for the same rows twice.
    strip_image = _render(mine, doc, [edit], [strip]).json()["images"][0]
    whole_page = _render(mine, doc, [edit], [{"page": 0}]).json()["images"][0]

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
    ["\n", "\r", "\t", "\ud800", "\u2028", "\u2029", "\u202a", "\u202e", "\u2066", "\u2069"],
    ids=[
        "line break",
        "carriage return",
        "tab",
        "half an emoji",
        "line separator",
        "paragraph separator",
        "first bidi embedding",
        "last bidi override",
        "first bidi isolate",
        "last bidi isolate",
    ],
)
def test_new_text_that_isnt_one_line_of_letters_is_a_bad_request(mine, doc, kind, character):
    """A line break would draw a second line over the next; half an emoji draws nothing.

    A bidi control draws nothing either, and makes the line read in another order than drawn.
    """
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


@pytest.mark.parametrize("kind", ["replace", "insert"])
@pytest.mark.parametrize(
    "character",
    ["\u200d", "\u200c", "\u202f", "\u206a"],
    ids=[
        "zero-width joiner",
        "zero-width non-joiner",
        "narrow no-break space, just past the bidi overrides",
        "the format character just past the bidi isolates",
    ],
)
def test_new_text_with_a_joiner_or_a_narrow_space_is_drawn(mine, doc, kind, character):
    """Real text, or a format character just outside the refused ranges."""
    span = span_starting(doc, 0, "Made")
    replace = {"kind": "replace", "span_id": span["id"], "text": f"Made{character}on"}
    insert = {**_INSERT, "text": f"Sig{character}ned"}
    edit = replace if kind == "replace" else insert

    response = _render(mine, doc, [edit], [{"page": 0}])

    assert_equal(response.status_code, 200, f"U+{ord(character):04X} drawn, not refused")


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


def test_more_regions_than_a_render_draws_are_a_bad_request(mine, doc, monkeypatch):
    """The browser asks for a strip per group of edited rows, so never more than its edits."""
    monkeypatch.setattr(editing_constants, "MAX_REGIONS", _LOWERED)
    strips = [{"page": 0, "y0": 100 * row, "y1": 100 * row + 50} for row in range(_LOWERED + 1)]

    response = _render(mine, doc, [], strips)

    assert_problem(response, "invalid_request", 400)
    assert_in("regions", response.json()["debug"], "what a developer reads")


@pytest.mark.parametrize(
    "regions",
    [
        [{"page": 0, "y0": 100, "y1": 150}, {"page": 0, "y0": 100, "y1": 150}],
        [{"page": 0, "y0": 100, "y1": 150}, {"page": 0}],
        [{"page": 0, "y0": 100, "y1": 150}, {"page": 0, "y0": 140, "y1": 200}],
        [{"page": 0, "y0": -5, "y1": 50}, {"page": 0, "y0": 0, "y1": 50}],
    ],
    ids=[
        "the same strip twice",
        "a strip and its page",
        "strips that overlap",
        "the same rows",
    ],
)
def test_regions_that_share_rows_on_a_page_are_a_bad_request(mine, doc, regions):
    """The browser joins rows that touch into one strip, so it never asks for a row twice.

    Drawn as asked, each would be the same rows again: many on one page outlast the timeout.
    """
    response = _render(mine, doc, [], regions)

    assert_problem(response, "invalid_request", 400)
    assert_in("regions", response.json()["debug"], "what a developer reads")


def test_strips_thinner_than_a_pixel_row_are_a_bad_request(mine):
    """Each is drawn out to whole rows, so slivers apart came back as many times the page.

    The page is as wide as a PDF's can be, so each of those rows is long.
    """
    wide = pymupdf.open()
    wide.new_page(width=_WIDEST_PT, height=_SHORT_PT)
    doc = upload(mine, wide.tobytes()).json()
    count = editing_constants.MAX_REGIONS
    cuts = [_SHORT_PT * row / count for row in range(count + 1)]
    slivers = [{"page": 0, "y0": top, "y1": bottom} for top, bottom in itertools.pairwise(cuts)]
    body = {"edits": [], "scale": max(api_constants.PAGE_SCALES), "regions": slivers}

    response = mine.post(f"/api/documents/{doc['id']}/render", json=body)

    assert_problem(response, "invalid_request", 400)
    assert_in("rows", response.json()["debug"], "what a developer reads")


@pytest.fixture
def tall_page(mine, tmp_path) -> dict:
    """The long contract's page four times, one under another on one page, as `mine` sent it.

    Every drawing of it runs four full pages of text, however few rows it fills.
    """
    path = tmp_path / "contract.pdf"
    write_dense(str(path), pages=1)
    contract = pymupdf.open(path)
    width, height = contract[0].rect.width, contract[0].rect.height
    tall = pymupdf.open()
    page = tall.new_page(width=width, height=height * _STACKED)
    for place in range(_STACKED):
        page.show_pdf_page(
            pymupdf.Rect(0, height * place, width, height * (place + 1)), contract
        )
    return upload(mine, tall.tobytes()).json()


def test_strips_apart_on_one_heavy_page_are_drawn_in_time(mine, tall_page):
    """Drawn one by one, each strip read the whole page again: this many outlasted the timeout.

    Thin and apart, as no browser sends them, but under the rows limit: none is refused.
    """
    scale = max(api_constants.PAGE_SCALES)
    params = {"scale": scale, "build": tall_page["build"]}
    page_png = mine.get(f"/api/documents/{tall_page['id']}/pages/0", params=params).content
    # Each strip is drawn out by under two rows, so this many stay within twice the page's.
    count = pymupdf.Pixmap(page_png).height // 2
    height = tall_page["pages"][0]["height"]
    cuts = [height * row / count for row in range(count + 1)]
    strips = [{"page": 0, "y0": top, "y1": bottom} for top, bottom in itertools.pairwise(cuts)]
    body = {"edits": [], "scale": scale, "regions": strips}

    response = mine.post(f"/api/documents/{tall_page['id']}/render", json=body)

    assert_equal(response.status_code, 200, "drawn within the render timeout")
    assert_equal(len(response.json()["images"]), count, "an image per strip")


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
