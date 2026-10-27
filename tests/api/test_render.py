"""Send edits and the rows on screen; get those rows drawn, a fit per replace, and the skips."""

from __future__ import annotations

import base64
import itertools
import json
import random
from pathlib import Path

import pymupdf
import pytest

from squidpdf.api import constants as api_constants
from squidpdf.core import COPY_PLACES, Engine, Message, words, write_dense
from squidpdf.core.constants import TOLERANCE_PT
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.documents import store
from squidpdf.documents.store import FolderStore
from squidpdf.editing import constants as editing_constants
from tests.api.conftest import (
    InProcess,
    around,
    edit_counts,
    export_lines,
    refusing_own_copies,
    span_starting,
    upload,
)
from tests.conftest import NAME_COPIES, REDACTED_NAME, in_a_copy_lacking, with_hidden_copies
from tests.helpers import assert_close, assert_equal, assert_in, assert_problem, assert_true

_SCALE = 2
_LONGER = "!!"  # a few points too long: every way out is offered
_OFF_GRID_PT = 80.3  # a strip edge between pixels at any scale
_LOWERED = 3  # the region limit, lowered so a short list goes over it
_WIDEST_PT = 14_400  # the longest side a PDF's page can have
_SHORT_PT = 72  # an inch: cut into the region limit's slivers, each far under a row
_STACKED = 4  # copies of the long contract's page, stacked on one tall page
_INSERT = {"kind": "insert", "page": 0, "origin": [72, 700], "text": "Signed", "size": 12}
_RED = (0.8, 0.1, 0.1)
# An axial shading, red to blue, from corner to corner of the box it fills below.
_SHADING = (
    "<</ShadingType 2/ColorSpace/DeviceRGB/Coords[250 40 340 180]"
    "/Function<</FunctionType 2/Domain[0 1]/C0[0.8 0.1 0.1]/C1[0.1 0.1 0.8]/N 1>>>>"
)
# Flipped to top-down points on a page 842 high: a box the shading fills, a triangle whose
# slants the strip cuts, and a box clipped by a peaked path, its off-grid sides cut square.
_ACROSS_THE_STRIP = (
    "q 1 0 0 -1 0 842 cm "
    "q 250.3 40.7 90 140 re W n /Sh sh Q "
    "0.1 0.6 0.2 rg 360.3 40.7 m 440.9 110.2 l 370.6 180.4 l h f "
    "q 460.37 60 m 505 40 l 549.61 60 l 549.61 180 l 460.37 180 l h W n "
    "0.2 0.2 0.8 rg 450 30 110 160 re f Q "
    "Q"
)


# The facts of `lacking_a_letter`: its one line, whose copy of its font has no "€".
_LACKING_LINE = "Total 14 €"


@pytest.fixture(scope="module")
def lacking_a_letter(tmp_path_factory) -> bytes:
    """One line whose stored copy of its font has no shape for the "€" it holds."""
    path = str(tmp_path_factory.mktemp("fx") / "lacking.pdf")
    return Path(in_a_copy_lacking(path, text=_LACKING_LINE, lacking="€")).read_bytes()


def _render(client, doc: dict, edits: list[dict], regions: list[dict], *, scale: int = _SCALE):
    """Ask for `regions` drawn with `edits`, as the browser does."""
    body = {"edits": edits, "scale": scale, "regions": regions}
    return client.post(f"/api/documents/{doc['id']}/render", json=body)


def _png(image: dict) -> bytes:
    """One of render's images, decoded."""
    return base64.b64decode(image["image"])


def _pixels(png: bytes) -> bytes:
    """An image's pixels in red, green and blue, however it was written: grey is one channel."""
    return pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Pixmap(png)).samples


def _in_color(pdf: bytes) -> bytes:
    """`pdf` with a red bar on page 0, across the strip drawn below: neither is grey."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    doc[0].draw_rect(pymupdf.Rect(72, 60, 300, 160), color=_RED, fill=_RED)
    return doc.tobytes()


def _drawn_across(pdf: bytes) -> bytes:
    """`pdf` with page 0 drawn across the strip below, in each way a clip changes the pixels."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[0]
    # A picture drawn from more pixels than it covers and one from fewer: two ways to resample.
    page.insert_image(pymupdf.Rect(20.3, 40.7, 120.1, 180.9), pixmap=_noise(300, 400))
    page.insert_image(pymupdf.Rect(130.3, 40.7, 230.1, 180.9), pixmap=_noise(17, 23))
    shading = doc.get_new_xref()
    doc.update_object(shading, _SHADING)
    resources = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])  # its own object
    doc.xref_set_key(resources, "Shading", f"<</Sh {shading} 0 R>>")
    drawing = doc.get_new_xref()
    doc.update_object(drawing, "<<>>")
    doc.update_stream(drawing, _ACROSS_THE_STRIP.encode())
    contents = " ".join(f"{xref} 0 R" for xref in [*page.get_contents(), drawing])
    doc.xref_set_key(page.xref, "Contents", f"[{contents}]")
    return doc.tobytes()


def _noise(width: int, height: int) -> pymupdf.Pixmap:
    """A picture of noise, from a fixed seed: any change in how it's resampled shows."""
    samples = random.Random(0).randbytes(width * height * 3)  # red, green and blue
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, False)


def test_a_replace_too_long_says_by_how_much_and_a_way_out_taken_says_nothing(mine, doc):
    span = span_starting(doc, 0, "Made")
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"] + _LONGER}

    fit = _render(mine, doc, [edit], []).json()["fits"][span["id"]]

    assert_true(fit["delta_pt"] > TOLERANCE_PT, f"{fit['delta_pt']} pt past the original")
    assert_equal(fit["options"], ["shrink", "condense", "as-is"], "the ways out")
    [part] = fit["message_parts"]
    percent = part["params"]["percent"]
    assert_equal(
        fit["message"], words.sentence("too_long").format(percent=percent), "the message"
    )
    # Said as a share of the line, to the next whole percent; the line is about its box wide.
    wide = span["bbox"]["x1"] - span["bbox"]["x0"]
    assert_close(
        percent, fit["delta_pt"] / wide * 100, 1, "how far past, as a share of the line"
    )
    # Taken, a way out ends the line where the original did: nothing runs long.
    shrunk = _render(mine, doc, [{**edit, "strategy": "shrink"}], []).json()["fits"][span["id"]]
    assert_equal((shrunk["strategy"], shrunk["message"]), ("shrink", None), "a way out taken")


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


@pytest.mark.parametrize("scale", api_constants.PAGE_SCALES)
@pytest.mark.parametrize(
    ("made", "written_in"),
    [(lambda pdf: pdf, "gray"), (_in_color, "color"), (_drawn_across, "color")],
    ids=["a gray page", "a color page", "pictures, a shading and paths the strip cuts"],
)
def test_rows_with_no_edits_are_the_page_image_exactly(
    mine, pdf_bytes, made, written_in, scale
):
    """Else a strip laid over the page image would show a seam.

    Each is the pixels MuPDF draws, though a page of grey is written in one channel. A clip
    changes how MuPDF draws pictures, shadings and the paths it cuts: a strip isn't drawn alone.
    """
    pdf = made(pdf_bytes)
    doc = upload(mine, pdf).json()
    delivery = span_starting(doc, 1, "Delivery")
    edit = {"kind": "replace", "span_id": delivery["id"], "text": "Delivery begins 2 March"}
    strip = {"page": 0, "y0": _OFF_GRID_PT, "y1": _OFF_GRID_PT + 60}

    # Asked apart: one request never asks for the same rows twice.
    strip_image = _render(mine, doc, [edit], [strip], scale=scale).json()["images"][0]
    whole_page = _render(mine, doc, [edit], [{"page": 0}], scale=scale).json()["images"][0]

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
    # The page's pixel rows under the strip; stride is the bytes in one row.
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
    """Each would draw over the next line, draw nothing, or reorder the line."""
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
    """The browser joins rows that touch into one strip, so it never asks for a row twice."""
    response = _render(mine, doc, [], regions)

    assert_problem(response, "invalid_request", 400)
    assert_in("regions", response.json()["debug"], "what a developer reads")


def test_strips_thinner_than_a_pixel_row_are_a_bad_request(mine):
    """Grown to whole rows, they'd be many times this page, as wide as a PDF's can be."""
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
    """The long contract's page, stacked on one tall page, as `mine` uploaded it.

    Every drawing of it runs every copy, however few rows it fills.
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
    """Thin strips within every limit: drawn one by one, they'd outlast the timeout."""
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
        # The file's own copy draws the words typed, as the page sets the line.
        (open_driver, ("exact", None), "edits_exact"),
        # The page refuses the file's own copy, so a similar font draws them.
        (refusing_own_copies, ("substitute", "Liberation Serif Regular"), "edits_substitute"),
    ],
    ids=["drawn in its own font", "on a page that refuses it"],
)
def test_an_edits_fit_says_how_it_is_drawn_as_its_export_counts_it(
    app, mine, lacking_a_letter, capsys, monkeypatch, driver_factory, verdict, counted
):
    """A span judged a similar font's at upload, for a letter its copy lacks, edited without it.

    Its note kept saying "a similar font", though the file and its log said otherwise.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    monkeypatch.setattr(app.state, "documents", FolderStore(driver_factory))
    doc = upload(mine, lacking_a_letter).json()
    [span] = doc["spans"]
    edits = [{"kind": "replace", "span_id": span["id"], "text": "Total 2"}]

    fit = _render(mine, doc, edits, [around(span)]).json()["fits"][span["id"]]
    counts = edit_counts(export_lines(mine, doc, edits, capsys))

    upload_verdict = (span["fidelity"], span["substitute"], span["in_file"])
    expected = ("substitute", "Liberation Serif Regular", True)
    assert_equal(upload_verdict, expected, "the span, judged on its own text, its copy usable")
    assert_equal((fit["fidelity"], fit["substitute"]), verdict, "the edit, as render says it")
    assert_equal(counts, {counted: "1"}, "the edit, as its export counts it")


_PAGE_FONT = "the page's"  # new text in the font the page's line is in


@pytest.mark.parametrize(
    ("driver_factory", "font", "text", "verdict"),
    [
        # No font of the document's to stand in for: it is what's asked.
        (open_driver, "Caveat Bold", "Signed", ("exact", None)),
        # A face we ship that lacks Ω: one that has it draws the line, as a similar font.
        (open_driver, "Caveat Bold", "Ω Smith", ("substitute", "Noto Sans Bold")),
        # In the page's own font, which draws it.
        (open_driver, _PAGE_FONT, "Paid", ("exact", None)),
        # In the page's own font, whose copy lacks "€": a similar font draws the line.
        (open_driver, _PAGE_FONT, "Paid €", ("substitute", "Liberation Serif Regular")),
        # In the page's own font, on a page that refuses it: a similar font draws it.
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

    [fit] = _render(mine, doc, [insert], [{"page": 0}]).json()["insert_fits"]

    assert_equal((fit["fidelity"], fit["substitute"]), verdict, "the new text's verdict")


def test_only_the_edits_in_the_rows_asked_for_are_redrawn(app, mine, doc, monkeypatch):
    """A strip over one line of a page with others edited: redrawing them all was most of it.

    Every replace still gets its fit: that's measurement, not drawing.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the patch below reaches it
    redrawn: list[str] = []
    draw = Engine.draw

    def noting_what(engine: Engine, span, text: str, **setting) -> object:
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
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's reads are counted
    reads: list[object] = []
    load_pages = store.load_pages

    def counted(folder, *args, **kwargs) -> list:
        reads.append(folder)
        return load_pages(folder, *args, **kwargs)

    monkeypatch.setattr(store, "load_pages", counted)

    response = _render(mine, doc, [], [{"page": 0}])

    assert_equal(response.status_code, 200, "render status")
    assert_equal(len(reads), 1, "times the page list was read")
