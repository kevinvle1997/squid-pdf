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
    "Justified text ends every line but its last at one edge, so none of them has any room "
    "to run into: a line made longer breaks the edge it shares with the lines around it, "
    "however much white space the page has beside it."
)
_FAR_X = 480  # a column far right on the paragraph's first line, which mustn't widen it
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
_COLUMNS = (
    ("Left column text that runs on", "Right column, line one"),
    ("mid length line", None),
    ("another left line", "Right column after a gap"),
)
# A second column 16 pt after the left one's first line: under the two ems a wide gap
# on a line needs, so that row reads as one line.
_GUTTER_X = 72 + pymupdf.get_text_length(_COLUMNS[0][0], fontsize=_SIZE) + 16


def _add_walls_page(doc: pymupdf.Document) -> None:
    """A page of what a longer line could run into, each where a paragraph's edge is far.

    A word set a little into the line's end, a word drawn turned across it, a table
    grid drawn as one path, a second column across a narrow gutter, and a rule down
    the page.
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
    for row, (left, right) in enumerate(_COLUMNS):
        page.insert_text((72, 400 + row * 15), left, fontsize=_SIZE)
        if right is not None:
            page.insert_text((_GUTTER_X, 400 + row * 15), right, fontsize=_SIZE)


@pytest.fixture
def room_doc(mine) -> dict:
    """The room page, uploaded by `mine`."""
    return upload(mine, _room_page()).json()


def _room_to(x: float, span: dict) -> float:
    """The room from where `span` ends to `x`."""
    return x - span["bbox"]["x1"]


def test_each_line_has_the_room_the_page_leaves_it(room_doc):
    """Up to what follows on its line, else its paragraph's widest line; none if justified."""
    first, second, _last = (span_starting(room_doc, 0, line) for line in _PROSE)
    label, *cell, boxed = (span_starting(room_doc, 0, s) for s in (_LABEL, *_CELL, _BOXED))
    justified = [s for s in room_doc["spans"] if s["page"] == 0 and 150 < s["bbox"]["y0"] < 260]
    rooms = {
        "a short line of a ragged paragraph": (second, _room_to(first["bbox"]["x1"], second)),
        "its widest line, up to the far column": (first, _room_to(_FAR_X, first)),
        "a label, up to its value": (label, _room_to(_VALUE_X, label)),
        "a cell's first line, up to the row's value": (cell[0], _room_to(_VALUE_X, cell[0])),
        "a cell's second line": (cell[1], _room_to(_VALUE_X, cell[1])),
        "a line, up to a box before the text after it": (boxed, _room_to(_BOX_X, boxed)),
    }

    for what, (span, room) in rooms.items():
        assert_close(span["room_pt"], room, 0.01, what)
    assert_at_least(len(justified), 3, "lines in the justified paragraph")
    assert_equal([span["room_pt"] for span in justified], [0] * len(justified), "justified")


def test_a_line_stops_at_whatever_a_longer_one_would_run_into(room_doc):
    """Not only text it half overlaps: a kerned word, turned text, a grid's line, a column."""
    kerned, turned_beside, turned, apple, mid, ruled = (
        span_starting(room_doc, 1, s)
        for s in (_KERNED[0], "short", "TURNED", _GRID[0], _COLUMNS[1][0], _RULED)
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
        "a second column across a narrow gutter": (mid, _room_to(_GUTTER_X, mid)),
        "a rule down the page": (ruled, _room_to(_RULE_X - _RULE_WIDTH / 2, ruled)),
    }

    for what, (span, room) in rooms.items():
        assert_close(span["room_pt"], room, 0.01, what)


def test_an_edit_into_its_room_fits_and_one_past_it_says_how_far_past(mine, room_doc):
    """The warning says the part that collides: past the paragraph's widest line."""
    span = span_starting(room_doc, 0, _PROSE[1])
    url = f"/api/documents/{room_doc['id']}/render"

    def fit_of(text: str) -> dict:
        """The server's fit for the span reading `text`."""
        edit = {"kind": "replace", "span_id": span["id"], "text": text}
        body = {"edits": [edit], "scale": 2, "regions": []}
        return mine.post(url, json=body).json()["fits"][span["id"]]

    fits_in_room = fit_of(span["text"] + " and more")
    runs_past = fit_of(span["text"] + " that now runs on well past the end of the longest line")

    said = (fits_in_room["message"], fits_in_room["options"])
    assert_equal(said, (None, []), "an edit into its room")
    past_by = round(runs_past["delta_pt"] - span["room_pt"], 2)
    too_long = words.render(Message("too_long", {"delta_pt": past_by}))
    assert_equal(runs_past["message"], too_long, "an edit past its room")
