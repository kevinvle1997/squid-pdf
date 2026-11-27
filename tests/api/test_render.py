from __future__ import annotations

import base64
import itertools
import json
import math
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, cast

import pymupdf
import pytest

from squidpdf.api import constants as api_constants
from squidpdf.core import COPY_PLACES, Message, words, write_dense
from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import CodeRun, TextRun
from squidpdf.documents import store
from squidpdf.documents.store import FolderStore
from squidpdf.editing import constants as editing_constants
from tests.api.conftest import (
    InProcess,
    MovingAfterAnErase,
    Place,
    around,
    drawn_page,
    edit_counts,
    export_lines,
    refusing_own_copies,
    render,
    span_starting,
    upload,
    verdicts_of,
)
from tests.conftest import (
    NAME_COPIES,
    REDACTED_NAME,
    each_letter,
    in_a_copy_lacking,
    with_hidden_copies,
)
from tests.helpers import (
    assert_at_least,
    assert_close,
    assert_equal,
    assert_in,
    assert_problem,
    assert_true,
)

_SCALE = 2
_LONGER = "!!"
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale
_LOWERED = 3
_REACT_EDITING = Path(__file__).parents[2] / "react" / "src" / "editing"
_WIDEST_PT = 14_400  # the longest side a PDF's page can have
_SHORT_PT = 72
_STACKED = 4
_INSERT = {"kind": "insert", "page": 0, "origin": [72, 700], "text": "Signed", "size": 12}
_RED = (0.8, 0.1, 0.1)
_SHADING = (
    "<</ShadingType 2/ColorSpace/DeviceRGB/Coords[250 40 340 180]"
    "/Function<</FunctionType 2/Domain[0 1]/C0[0.8 0.1 0.1]/C1[0.1 0.1 0.8]/N 1>>>>"
)
_ACROSS_THE_STRIP = (
    "q 1 0 0 -1 0 842 cm "
    "q 250.3 40.7 90 140 re W n /Sh sh Q "
    "0.1 0.6 0.2 rg 360.3 40.7 m 440.9 110.2 l 370.6 180.4 l h f "
    "q 460.37 60 m 505 40 l 549.61 60 l 549.61 180 l 460.37 180 l h W n "
    "0.2 0.2 0.8 rg 450 30 110 160 re f Q "
    "Q"
)


_LACKING_LINE = "Total 14 €"


@pytest.fixture(scope="module")
def lacking_a_letter(tmp_path_factory) -> bytes:
    path = str(tmp_path_factory.mktemp("fx") / "lacking.pdf")
    return Path(in_a_copy_lacking(path, text=_LACKING_LINE, lacking="€")).read_bytes()


def _png(image: dict) -> bytes:
    return base64.b64decode(image["image"])


def _pixels(png: bytes) -> bytes:
    return pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Pixmap(png)).samples


def _in_color(pdf: bytes) -> bytes:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    doc[0].draw_rect(pymupdf.Rect(72, 60, 300, 160), color=_RED, fill=_RED)
    return doc.tobytes()


def _drawn_across(pdf: bytes) -> bytes:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[0]
    page.insert_image(pymupdf.Rect(20.3, 40.7, 120.1, 180.9), pixmap=_noise(300, 400))
    page.insert_image(pymupdf.Rect(130.3, 40.7, 230.1, 180.9), pixmap=_noise(17, 23))
    shading = doc.get_new_xref()
    doc.update_object(shading, _SHADING)
    resources = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
    doc.xref_set_key(resources, "Shading", f"<</Sh {shading} 0 R>>")
    drawing = doc.get_new_xref()
    doc.update_object(drawing, "<<>>")
    doc.update_stream(drawing, _ACROSS_THE_STRIP.encode())
    contents = " ".join(f"{xref} 0 R" for xref in [*page.get_contents(), drawing])
    doc.xref_set_key(page.xref, "Contents", f"[{contents}]")
    return doc.tobytes()


def _noise(width: int, height: int) -> pymupdf.Pixmap:
    samples = random.Random(0).randbytes(width * height * 3)
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, False)


def test_a_replace_too_long_says_by_how_much_and_a_way_out_taken_says_nothing(mine, doc):
    span = span_starting(doc, 0, "Made")
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"] + _LONGER}

    fit = render(mine, doc, [edit], []).json()["fits"][span["id"]]

    past_tolerance = math.nextafter(TOLERANCE_PT, math.inf)
    assert_at_least(fit["delta_pt"], past_tolerance, "points past the original")
    assert_equal(fit["options"], ["shrink", "condense", "as-is"], "the ways out")
    [part] = fit["message_parts"]
    percent = part["params"]["percent"]
    assert_equal(
        fit["message"], words.sentence("too_long").format(percent=percent), "the message"
    )
    wide = span["bbox"]["x1"] - span["bbox"]["x0"]
    assert_close(
        percent, fit["delta_pt"] / wide * 100, 1, "how far past, as a share of the line"
    )
    shrunk = render(mine, doc, [{**edit, "strategy": "shrink"}], []).json()["fits"][span["id"]]
    assert_equal((shrunk["strategy"], shrunk["message"]), ("shrink", None), "a way out taken")


_EVERY_PLACE_SAID = " ".join(
    [
        words.render(
            Message(
                "hidden_copies",
                {"places": [words.sentence(f"place_{place}") for place in COPY_PLACES]},
            )
        ),
        words.render(Message("signatures_removed")),
    ]
)


@pytest.mark.parametrize(
    ("hidden_copies", "named", "said"),
    [
        ([*NAME_COPIES, "signature"], [*COPY_PLACES, "signatures"], _EVERY_PLACE_SAID),
        ((), [], None),
    ],
    ids=["kept in every place, and signed", "kept nowhere else"],
)
def test_a_redaction_drawn_names_each_place_the_file_also_kept_its_words(
    mine, hidden_copies, named, said
):
    doc = upload(mine, with_hidden_copies(hidden_copies)).json()
    name = span_starting(doc, 0, REDACTED_NAME)

    rendered = render(mine, doc, [{"kind": "redact", "span_id": name["id"]}], [around(name)])

    expected = {"verified": True, "hidden_copies": named, "message": said}
    assert_equal(verdicts_of(rendered.json()), {name["id"]: expected}, "what render says")


def _said_of(rendered: dict, span: dict, rest: list[Message]) -> str | None:
    """Each a whole sentence, so they join as sentences do, not as the parts of one."""
    places = rendered["redactions"][span["id"]]["hidden_copies"]
    named = [words.sentence(f"place_{place}", words.ENGLISH) for place in places]
    hidden = [Message("hidden_copies", {"places": named})] if named else []
    said = [words.render(message) for message in rest + hidden]
    return " ".join(said) or None


def test_words_redacted_whose_erase_moves_the_rest_of_the_line_say_so_first(
    app, mine, monkeypatch
):
    monkeypatch.setattr(app.state, "documents", FolderStore(MovingAfterAnErase(_all_along)))
    doc = upload(mine, drawn_page(f"BT /F 12 Tf 72 100 Td {_ACCOUNT}", pages=2)).json()
    first, second = span_starting(doc, 0, "Acct"), span_starting(doc, 1, "Acct")
    edits = [
        {"kind": "redact", "span_id": first["id"], "range": [5, 9]},
        {"kind": "redact", "span_id": second["id"], "range": [5, 9]},
    ]

    rendered = render(mine, doc, edits, [{"page": 0}, {"page": 1}]).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    moved = [
        Message("rest_of_line_moved", {"text": "1234", "page": 1}),
        Message("rest_of_line_moved", {"text": "1234", "page": 2}),
    ]
    said = rendered["redactions"][first["id"]]
    expected = (True, _said_of(rendered, first, moved[:1]))
    assert_equal((said["verified"], said["message"]), expected, "what render says of it")
    under_the_bar = [notice["detail"] for notice in rendered["notices"]]
    assert_equal(
        under_the_bar, [words.render(each) for each in moved], "the lines under the bar"
    )
    saved = pymupdf.open(stream=exported.content, filetype="pdf")
    page_texts = [page.get_text() for page in saved.pages()]
    assert_equal(
        ["1234" in text for text in page_texts], [False, False], "the file, its words gone"
    )


def _all_along(_origin: Place) -> Place:
    return (2.0, 0.0)


def _rest_along(origin: Place) -> Place:
    return (2.0, 0.0) if origin[0] > 100 else _STILL


def _rest_off(origin: Place) -> Place:
    return (0.0, 3.0) if origin[0] > 100 else _STILL


_STILL = (0.0, 0.0)


@pytest.mark.parametrize(
    ("moves", "said"),
    [
        (_rest_along, "rest_of_line_moved"),
        (_rest_off, "moved_other_text"),
    ],
    ids=[
        "after the words along it, before left, said",
        "off it, refused",
    ],
)
def test_the_rest_of_a_line_set_along_it_downloads_saying_so_and_set_off_it_doesnt(
    app, mine, monkeypatch, moves, said
):
    monkeypatch.setattr(app.state, "documents", FolderStore(MovingAfterAnErase(moves)))
    doc = upload(mine, drawn_page(f"BT /F 12 Tf 72 100 Td {_ACCOUNT}")).json()
    span = span_starting(doc, 0, "Acct")
    edits = [{"kind": "redact", "span_id": span["id"], "range": [5, 9]}]

    rendered = render(mine, doc, edits, [{"page": 0}]).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    verdict = rendered["redactions"][span["id"]]
    expected = words.render(Message(said, {"text": "1234", "page": 1}))
    assert_equal(verdict["message"], expected, "what render says of it")
    downloads = said == "rest_of_line_moved"
    assert_equal((verdict["verified"], exported.status_code == 200), (downloads,) * 2, "goes")


_ACCOUNT = "(Acct 1234 Name) Tj ET"


@pytest.mark.parametrize(
    ("drawing", "starts", "asked", "rest"),
    [
        ("BT /F 12 Tf 72 100 Td [(Acct) -333 (1234) -333 (Name)] TJ ET", "Acct", [5, 9], []),
        ("BT /F 12 Tf 72 100 Td [(Acct) -333 (1234) -333 (Name)] TJ ET", "Acct", [10, 14], []),
        ("BT /F 12 Tf 2 Tc 72 100 Td " + _ACCOUNT, "A", [10, 17], []),
        (
            f"/Span <</ActualText (Acct 1234 Name)>> BDC BT /F 12 Tf 72 100 Td {_ACCOUNT} EMC",
            "Acct",
            [5, 9],
            [],
        ),
        (
            f"BT /F 12 Tf 72 100 Td {_ACCOUNT} BT /F 12 Tf 72.4 100 Td {_ACCOUNT}",
            "Acct",
            [5, 9],
            [],
        ),
        (
            "BT /F 12 Tf 0.7071 0.7071 -0.7071 0.7071 72 72 Tm " + _ACCOUNT,
            "Acct",
            [5, 9],
            [Message("rest_of_line_removed", {"text": "1234", "page": 1})],
        ),
        (
            "BT /F 12 Tf 72 100 Td (Acct ) Tj /Span <</ActualText (9999)>> BDC (1234) Tj EMC "
            "( Name) Tj ET",
            "Acct",
            [5, 9],
            [Message("rest_of_line_removed", {"text": "9999", "page": 1})],
        ),
    ],
    ids=[
        "spaced out with no space drawn",
        "its last word, spaced out",
        "letters set apart",
        "in marked content",
        "drawn twice over",
        "at an angle: letters past the words go too",
        "marked content that says other words: the whole line goes",
    ],
)
def test_what_a_redactions_erase_did_to_the_rest_of_its_line_is_said_only_when_true(
    mine, drawing, starts, asked, rest
):
    doc = upload(mine, drawn_page(drawing)).json()
    span = span_starting(doc, 0, starts)
    edits = [{"kind": "redact", "span_id": span["id"], "range": asked}]

    rendered = render(mine, doc, edits, [{"page": 0}]).json()

    said = rendered["redactions"][span["id"]]
    assert_equal(said["verified"], True, "whether the words are gone")
    assert_equal(said["message"], _said_of(rendered, span, rest), "what render says of it")


@pytest.mark.parametrize(
    "turn",
    ["1 0 0 1 72 100 Tm", "0 1 -1 0 100 72 Tm", "-1 0 0 -1 300 100 Tm"],
    ids=["level", "a quarter turn", "upside down"],
)
def test_each_run_of_words_redacted_comes_with_the_box_its_letters_take(mine, turn):
    sent = drawn_page(f"BT /F 12 Tf {turn} (Acct 1234 Name) Tj ET")
    doc = upload(mine, sent).json()
    span = span_starting(doc, 0, "Acct")
    edits = [{"kind": "redact", "span_id": span["id"], "range": [5, 9]}]

    rendered = render(mine, doc, edits, [{"page": 0}]).json()

    [run] = rendered["redactions"][span["id"]]["runs"]
    assert_equal(run["range"], [5, 9], "the run's words, by code point")
    box = run["box"]
    page = pymupdf.open(stream=sent, filetype="pdf")[0]
    for char in each_letter(page.get_text("rawdict")["blocks"]):
        x0, y0, x1, y1 = char["bbox"]
        middle = ((x0 + x1) / 2, (y0 + y1) / 2)
        inside = box["x0"] <= middle[0] <= box["x1"] and box["y0"] <= middle[1] <= box["y1"]
        assert_equal(inside, char["c"] in "1234", f"whether {char['c']!r} is in the box")


def test_new_text_over_the_line_moves_none_of_the_rest_of_it(mine):
    doc = upload(mine, drawn_page(f"BT /F 12 Tf 72 100 Td {_ACCOUNT}")).json()
    span = span_starting(doc, 0, "Acct")
    box = span["bbox"]
    insert = {**_INSERT, "origin": [box["x0"] + 28, box["y1"] - 3], "text": "XXXX"}
    edits = [{"kind": "redact", "span_id": span["id"], "range": [5, 9]}, insert]

    rendered = render(mine, doc, edits, [{"page": 0}]).json()

    said = rendered["redactions"][span["id"]]
    assert_equal((said["verified"], said["message"]), (True, None), "what render says of it")


def test_every_place_a_file_keeps_a_hidden_copy_has_its_word():
    for place in COPY_PLACES:
        assert_in(f"place_{place}", words.CATALOGS[words.ENGLISH], "the places named")


@pytest.mark.parametrize("scale", api_constants.PAGE_SCALES)
@pytest.mark.parametrize(
    ("made", "written_in"),
    [(lambda pdf: pdf, "gray"), (_in_color, "color"), (_drawn_across, "color")],
    ids=["a gray page", "a color page", "pictures, a shading and paths the strip cuts"],
)
def test_rows_with_no_edits_are_the_page_image_exactly(
    mine, pdf_bytes, made, written_in, scale
):
    pdf = made(pdf_bytes)
    doc = upload(mine, pdf).json()
    delivery = span_starting(doc, 1, "Delivery")
    edit = {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"}
    strip = {"page": 0, "y0": _OFF_GRID_PT, "y1": _OFF_GRID_PT + 60}

    strip_image = render(mine, doc, [edit], [strip], scale=scale).json()["images"][0]
    whole_page = render(mine, doc, [edit], [{"page": 0}], scale=scale).json()["images"][0]

    params = {"scale": scale, "build": doc["build"]}
    page_png = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params).content
    assert_equal(_png(whole_page), page_png, "the whole page, against the page image")
    matrix = pymupdf.Matrix(scale, scale)
    drawn = pymupdf.open(stream=pdf, filetype="pdf")[0].get_pixmap(matrix=matrix, alpha=False)
    assert_true(_pixels(page_png) == drawn.samples, "the page image is the page drawn")
    # A case tests its path only if its page takes it: gray in one channel, color in three.
    taken = "gray" if pymupdf.Pixmap(page_png).n == 1 else "color"
    assert_equal(taken, written_in, "the path the fixture's page image took")
    strip_png = _png(strip_image)
    rows = pymupdf.Pixmap(strip_png)
    top = round(strip_image["y"] * scale)
    expected = drawn.samples[top * drawn.stride : (top + rows.height) * drawn.stride]
    assert_equal(rows.width, drawn.width, "strip width")
    assert_true(
        _pixels(strip_png) == expected, f"the strip at y={strip_image['y']} matches its rows"
    )


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"size": 0}, "size"),
        ({"color": [5, 0, 0]}, "color"),
    ],
    ids=["size 0", "a color past 1"],
)
def test_an_insert_nothing_can_draw_is_a_bad_request(mine, doc, change, field):
    response = render(mine, doc, [{**_INSERT, **change}], [{"page": 0}])
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
    span = span_starting(doc, 0, "Made")
    replace = {"kind": "replace", "span_id": span["id"], "text": f"Made{character}on"}
    insert = {**_INSERT, "text": f"Sig{character}ned"}
    edit = replace if kind == "replace" else insert

    response = render(mine, doc, [edit], [{"page": 0}])

    assert_equal(response.status_code, 200, f"U+{ord(character):04X} drawn, not refused")


def test_an_edit_that_doesnt_say_its_kind_is_a_bad_request(mine, doc):
    span = span_starting(doc, 0, "Made")

    response = render(mine, doc, [{"span_id": span["id"], "text": "x"}], [{"page": 0}])

    assert_problem(response, "invalid_request", 400)
    assert_in("kind", response.json()["debug"], "what a developer reads")


@pytest.mark.parametrize(
    "region",
    [{"page": 0, "y0": 900, "y1": 1000}, {"page": 0, "y0": -100, "y1": -50}],
    ids=["below the page", "above it"],
)
def test_a_strip_off_the_page_is_a_bad_request(mine, doc, region):
    assert_problem(render(mine, doc, [], [region]), "invalid_request", 400)


def test_more_regions_than_a_render_draws_are_a_bad_request(mine, doc, monkeypatch):
    monkeypatch.setattr(editing_constants, "MAX_REGIONS", _LOWERED)
    strips = [{"page": 0, "y0": 100 * row, "y1": 100 * row + 50} for row in range(_LOWERED + 1)]

    response = render(mine, doc, [], strips)

    assert_problem(response, "invalid_request", 400)
    assert_in("regions", response.json()["debug"], "what a developer reads")


def test_regions_on_more_pages_than_a_render_draws_are_a_bad_request(mine, doc, monkeypatch):
    monkeypatch.setattr(editing_constants, "MAX_RENDER_PAGES", 1)
    pages = [{"page": 0, "y0": 100, "y1": 150}, {"page": 1, "y0": 100, "y1": 150}]

    response = render(mine, doc, [], pages)

    assert_problem(response, "invalid_request", 400)
    assert_in("pages", response.json()["debug"], "what a developer reads")


def test_the_browser_asks_for_no_more_pages_than_a_render_draws():
    constants_ts = (_REACT_EDITING / "constants.ts").read_text()
    batch = re.search(r"export const RENDER_PAGES_AT_ONCE = (\d+);", constants_ts)
    assert_true(batch is not None, "react/src/editing/constants.ts names RENDER_PAGES_AT_ONCE")
    said = "pages the browser asks for at once (RENDER_PAGES_AT_ONCE) beside the server's limit"
    assert_equal(int(batch[1]) if batch else None, editing_constants.MAX_RENDER_PAGES, said)


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
    response = render(mine, doc, [], regions)

    assert_problem(response, "invalid_request", 400)
    assert_in("regions", response.json()["debug"], "what a developer reads")


def test_strips_thinner_than_a_pixel_row_are_a_bad_request(mine):
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


@pytest.mark.parametrize(
    ("driver_factory", "verdict", "counted"),
    [
        (open_driver, ("exact", None), "edits_exact"),
        (refusing_own_copies, ("substitute", "Liberation Serif Regular"), "edits_substitute"),
    ],
    ids=["drawn in its own font", "on a page that refuses it"],
)
def test_an_edits_fit_says_how_it_is_drawn_as_its_export_counts_it(
    app, mine, lacking_a_letter, capsys, monkeypatch, driver_factory, verdict, counted
):
    monkeypatch.setattr(app.state, "pool", InProcess())
    monkeypatch.setattr(app.state, "documents", FolderStore(driver_factory))
    doc = upload(mine, lacking_a_letter).json()
    [span] = doc["spans"]
    edits = [{"kind": "replace", "span_id": span["id"], "text": "Total 2"}]

    fit = render(mine, doc, edits, [around(span)]).json()["fits"][span["id"]]
    counts = edit_counts(export_lines(mine, doc, edits, capsys))

    upload_verdict = (span["fidelity"], span["substitute"], span["in_file"])
    expected = ("substitute", "Liberation Serif Regular", True)
    assert_equal(upload_verdict, expected, "the span, judged on its own text, its copy usable")
    assert_equal((fit["fidelity"], fit["substitute"]), verdict, "the edit, as render says it")
    assert_equal(counts, {counted: "1"}, "the edit, as its export counts it")


_PAGE_FONT = "the page's"


@pytest.mark.parametrize(
    ("driver_factory", "font", "text", "verdict"),
    [
        (open_driver, "Caveat Bold", "Signed", ("exact", None)),
        (open_driver, "Caveat Bold", "Ω Smith", ("substitute", "Noto Sans Bold")),
        (open_driver, _PAGE_FONT, "Paid", ("exact", None)),
        (open_driver, _PAGE_FONT, "Paid €", ("substitute", "Liberation Serif Regular")),
        (refusing_own_copies, _PAGE_FONT, "Paid", ("substitute", "Liberation Serif Regular")),
    ],
)
def test_new_texts_fit_says_how_it_is_drawn_as_a_replaces_does(
    app, mine, lacking_a_letter, monkeypatch, driver_factory, font, text, verdict
):
    monkeypatch.setattr(app.state, "documents", FolderStore(driver_factory))
    doc = upload(mine, lacking_a_letter).json()
    [span] = doc["spans"]
    insert = {**_INSERT, "text": text, "font": span["font"] if font == _PAGE_FONT else font}

    [fit] = render(mine, doc, [insert], [{"page": 0}]).json()["insert_fits"]

    assert_equal((fit["fidelity"], fit["substitute"]), verdict, "the new text's verdict")


type Written = tuple[int, tuple[float, float]]


@dataclass(frozen=True, slots=True, eq=False)
class _NotingDriver:
    driver: PdfDriver
    written: list[Written]

    def write_text(self, page: int, *, runs: Sequence[TextRun], **setting: Any) -> None:
        self.written.append((page, runs[0].origin))
        self.driver.write_text(page, runs=runs, **setting)

    def write_codes(
        self, page: int, *, origin: tuple[float, float], runs: Sequence[CodeRun], **setting: Any
    ) -> None:
        self.written.append((page, origin))
        self.driver.write_codes(page, origin=origin, runs=runs, **setting)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _noting_driver(path: str, *, written: list[Written]) -> PdfDriver:
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _NotingDriver(open_driver(path), written))


def test_only_the_edits_in_the_rows_asked_for_are_redrawn(app, mine, doc, monkeypatch):
    monkeypatch.setattr(app.state, "pool", InProcess())
    redrawn: list[Written] = []
    noting = partial(_noting_driver, written=redrawn)
    monkeypatch.setattr(app.state, "documents", FolderStore(noting))
    delivery, invoices = span_starting(doc, 1, "Delivery"), span_starting(doc, 1, "Invoices")
    edits = [
        {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"},
        {"kind": "replace", "span_id": invoices["id"], "text": "Invoices are due at once."},
    ]
    fits = render(mine, doc, edits, [around(invoices)]).json()["fits"]

    assert_equal(redrawn, [(1, tuple(invoices["origin"]))], "where each line redrawn starts")
    assert_equal(set(fits), {delivery["id"], invoices["id"]}, "the replaces with a fit")


def test_render_reads_the_page_list_once(app, mine, doc, monkeypatch):
    monkeypatch.setattr(app.state, "pool", InProcess())
    reads: list[object] = []
    load_pages = store.load_pages

    def counted(folder, *args, **kwargs) -> list:
        reads.append(folder)
        return load_pages(folder, *args, **kwargs)

    monkeypatch.setattr(store, "load_pages", counted)

    response = render(mine, doc, [], [{"page": 0}])

    assert_equal(response.status_code, 200, "render status")
    assert_equal(len(reads), 1, "times the page list was read")
