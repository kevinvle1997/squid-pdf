"""A line is too long only once it runs into something: its room, read from the page."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import Message, words
from tests.api.conftest import span_starting, upload
from tests.helpers import assert_at_least, assert_close, assert_equal

_SIZE = 10
_LEADING = 14  # a paragraph's lines, this far apart
_PROSE = (
    "A paragraph set ragged right, its first line the longest of the three",
    "Second line",
    "and its last",
)
_JUSTIFIED = (
    "Justified text ends every line but its last at one edge, and a line made longer may "
    "still run past it into the white space beside it, as far as the page's rightmost "
    "text, since it overlaps nothing on the way there."
)
_FAR_X = 480  # a column far right on the paragraph's first line: the page's rightmost text
_LABEL, _VALUE = "Delivery", "$12.00"
_CELL = ("Large widget,", "boxed")  # a description over two lines
_ROW_VALUE = "1"  # the row's value, set between the description's two lines
_BOXED = "Boxed in"
_BOX_X = 200  # where a filled box starts, level with `_BOXED`
_AFTER_X = 330  # where the text after the box starts
_VALUE_X = 300  # where the label's value and the row's value start


def _room_page() -> bytes:
    """One page with each kind of room.

    A ragged paragraph, a column far right of its first line, a justified one, a label
    and its value, a two-line cell whose row value sits between its lines, overlapping
    each by less than half, and a line with a filled box before the text after it.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    for row, line in enumerate(_PROSE):
        page.insert_text((72, 100 + row * _LEADING), line, fontsize=_SIZE)
    page.insert_text((_FAR_X, 100), _VALUE, fontsize=_SIZE)
    page.insert_textbox(
        pymupdf.Rect(72, 160, 400, 260),
        _JUSTIFIED,
        fontsize=_SIZE,
        align=pymupdf.TEXT_ALIGN_JUSTIFY,
    )
    page.insert_text((72, 300), _LABEL, fontsize=_SIZE)
    page.insert_text((_VALUE_X, 300), _VALUE, fontsize=_SIZE)
    page.insert_text((72, 340), _CELL[0], fontsize=_SIZE)
    page.insert_text((72, 358), _CELL[1], fontsize=_SIZE)
    page.insert_text((_VALUE_X, 349), _ROW_VALUE, fontsize=_SIZE)
    page.insert_text((72, 400), _BOXED, fontsize=_SIZE)
    page.insert_text((_AFTER_X, 400), _VALUE, fontsize=_SIZE)
    page.draw_rect(
        pymupdf.Rect(_BOX_X, 388, _BOX_X + 100, 404), color=None, fill=(0.9, 0.9, 0.9)
    )
    _add_walls_page(doc)
    return doc.tobytes()


_LONG = "A long line above each, so its paragraph's edge is far to the right of it"
_KERNED = ("Total ", "Amount")  # the second set a little into the first, as a kern sets it
_TURNED_X = 120  # where a word drawn turned crosses the line beside it
_GRID = ("Apples", "Notes: a row that runs across both of the grid's columns")
_CELL_X = 140  # the grid's inner border, drawn in one path with the rest of it
_GRID_STROKE = 0.5  # its lines' width: half of it is ink before the border
_RULE_X = 200  # a rule down the page, many lines tall
_RULE_WIDTH = 1.0  # half of it is ink before the rule's line
_RULED = "beside a rule"


def _add_walls_page(doc: pymupdf.Document) -> None:
    """A page of what a longer line could run into, each where the page's margin is far.

    A word set a little into the line's end, a word drawn turned across it, a table
    grid drawn as one path, and a rule down the page.
    """
    page = doc.new_page()
    page.insert_text((72, 86), _LONG, fontsize=_SIZE)
    first_width = pymupdf.get_text_length(_KERNED[0], fontsize=_SIZE)
    page.insert_text((72, 100), _KERNED[0], fontsize=_SIZE)
    page.insert_text((72 + first_width - 0.3, 100), _KERNED[1], fontsize=_SIZE, fontname="hebo")
    page.insert_text((72, 200), _LONG, fontsize=_SIZE)
    page.insert_text((72, 214), "short", fontsize=_SIZE)
    page.insert_text((_TURNED_X, 230), "TURNED", fontsize=_SIZE, rotate=90)
    grid = page.new_shape()
    for row in range(2):
        top = 288 + row * 20
        grid.draw_rect(pymupdf.Rect(70, top, _CELL_X, top + 20))
        grid.draw_rect(pymupdf.Rect(_CELL_X, top, 300, top + 20))
    grid.finish(color=(0, 0, 0), width=_GRID_STROKE)
    grid.commit()
    for row, text in enumerate(_GRID):
        page.insert_text((74, 300 + row * 20), text, fontsize=_SIZE)
    page.insert_text((72, 500), _LONG, fontsize=_SIZE)
    page.insert_text((72, 514), _RULED, fontsize=_SIZE)
    page.draw_line((_RULE_X, 480), (_RULE_X, 700), width=_RULE_WIDTH)


@pytest.fixture
def room_doc(mine) -> dict:
    """The room page, uploaded by `mine`."""
    return upload(mine, _room_page()).json()


def _room_to(x: float, span: dict) -> float:
    """The room from where `span` ends to `x`."""
    return x - span["bbox"]["x1"]


def _margin_of(doc: dict, page: int) -> float:
    """Where the rightmost text on `page` ends."""
    return max(span["bbox"]["x1"] for span in doc["spans"] if span["page"] == page)


def test_each_line_has_the_room_the_page_leaves_it(room_doc):
    """Up to what it would overlap, else the page's rightmost text, justified or not."""
    first, second, _last = (span_starting(room_doc, 0, line) for line in _PROSE)
    label, *cell, boxed = (span_starting(room_doc, 0, s) for s in (_LABEL, *_CELL, _BOXED))
    justified = [s for s in room_doc["spans"] if s["page"] == 0 and 150 < s["bbox"]["y0"] < 260]
    margin = _margin_of(room_doc, 0)
    rooms = {
        "a short line, past its paragraph's widest": (second, _room_to(margin, second)),
        "its widest line, up to the far column": (first, _room_to(_FAR_X, first)),
        "a label, up to its value": (label, _room_to(_VALUE_X, label)),
        "a cell's first line, up to the row's value": (cell[0], _room_to(_VALUE_X, cell[0])),
        "a cell's second line": (cell[1], _room_to(_VALUE_X, cell[1])),
        "a line, up to a box before the text after it": (boxed, _room_to(_BOX_X, boxed)),
    }

    for what, (span, room) in rooms.items():
        assert_close(span["room_pt"], room, 0.01, what)
    assert_at_least(len(justified), 3, "lines in the justified paragraph")
    for line in justified:
        assert_close(line["room_pt"], _room_to(margin, line), 0.01, "a justified line")


def test_a_line_stops_at_whatever_a_longer_one_would_run_into(room_doc):
    """Not only text it half overlaps: a kerned word, turned text, a grid's line, a rule."""
    kerned, turned_beside, turned, apple, ruled = (
        span_starting(room_doc, 1, s) for s in (_KERNED[0], "short", "TURNED", _GRID[0], _RULED)
    )
    rooms = {
        "a word set a little into its end": (kerned, 0),
        "a word drawn turned across it": (
            turned_beside,
            _room_to(turned["bbox"]["x0"], turned_beside),
        ),
        "a grid's inner line, drawn in one path": (
            apple,
            _room_to(_CELL_X - _GRID_STROKE / 2, apple),
        ),
        "a rule down the page": (ruled, _room_to(_RULE_X - _RULE_WIDTH / 2, ruled)),
    }

    for what, (span, room) in rooms.items():
        assert_close(span["room_pt"], room, 0.01, what)


def test_an_edit_into_its_room_fits_and_one_past_it_says_how_far_past(mine, room_doc):
    """Past its paragraph's widest line fits; the warning says the part past the margin."""
    span = span_starting(room_doc, 0, _PROSE[1])
    url = f"/api/documents/{room_doc['id']}/render"

    def fit_of(text: str) -> dict:
        """The server's fit for the span reading `text`."""
        edit = {"kind": "replace", "span_id": span["id"], "text": text}
        body = {"edits": [edit], "scale": 2, "regions": []}
        return mine.post(url, json=body).json()["fits"][span["id"]]

    fits_in_room = fit_of(
        span["text"] + " runs on past its paragraph's widest line, into the space"
    )
    runs_past = fit_of(
        span["text"]
        + " runs on past its paragraph's widest line, and on past the far column at "
        "the right of the page too, as far as the edge of the paper and beyond it"
    )

    said = (fits_in_room["message"], fits_in_room["options"])
    assert_equal(said, (None, []), "an edit into its room")
    past_by = round(runs_past["delta_pt"] - span["room_pt"], 2)
    too_long = words.render(Message("too_long", {"delta_pt": past_by}))
    assert_equal(runs_past["message"], too_long, "an edit past its room")
