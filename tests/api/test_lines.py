from __future__ import annotations

import pymupdf
import pytest

from tests.api.conftest import span_starting, upload
from tests.conftest import SENTENCE, write_runs
from tests.helpers import assert_at_least, assert_close, assert_equal

_SIZE = 10
_LEFT = 72
_FAR_X = 480
_CELLS = ("Apples", "12")
_CELL_X = 300
_UNDERLINED = (
    ("Please read ", "helv"),
    ("the", "hebo"),
    ("terms", "heit"),
    (" today.", "helv"),
)
_LINKED = (("Please see ", "helv"), ("our", "hebo"), ("site", "heit"), (" for more.", "helv"))
_SQUARED = ("E = mc", "2")
_TWICE = "Total"  # drawn twice, a little apart, as a bold the font doesn't have is faked
_DUE = " due"
_BIG, _SMALL = ("BIG", 20), (" small", 8)
_BOX_X = 300


def _lines_page() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    write_runs(page, SENTENCE, origin=(_LEFT, 100), size=_SIZE)
    page.insert_text((_FAR_X, 100), "far", fontsize=_SIZE)
    page.insert_text((_LEFT, 140), _CELLS[0], fontsize=_SIZE)
    page.insert_text((_CELL_X, 140), _CELLS[1], fontsize=_SIZE, fontname="hebo")
    underlined = write_runs(page, _UNDERLINED, origin=(_LEFT, 180), size=_SIZE)
    page.draw_line((underlined[2], 181), (underlined[3], 181), width=0.5)
    linked = write_runs(page, _LINKED, origin=(_LEFT, 220), size=_SIZE)
    area = pymupdf.Rect(linked[2], 210, linked[3], 222)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": area, "uri": "https://example.com/"})
    page.insert_text((_LEFT, 260), _SQUARED[0], fontsize=_SIZE)
    squared_x = _LEFT + pymupdf.get_text_length(_SQUARED[0], fontsize=_SIZE)
    page.insert_text((squared_x, 256), _SQUARED[1], fontsize=6)
    _write_twice(page, 300)
    _write_big_and_small(page, 360)
    return doc.tobytes()


def _write_twice(page: pymupdf.Page, baseline: float) -> None:
    page.insert_text((_LEFT, baseline), _TWICE, fontsize=_SIZE, fontname="hebo")
    page.insert_text((_LEFT + 0.4, baseline), _TWICE, fontsize=_SIZE, fontname="hebo")
    after = _LEFT + 0.4 + pymupdf.get_text_length(_TWICE, fontname="hebo", fontsize=_SIZE)
    page.insert_text((after, baseline), _DUE, fontsize=_SIZE)


def _write_big_and_small(page: pymupdf.Page, baseline: float) -> None:
    """The box is level with the big word only, so the small one's room runs past it."""
    page.insert_text((_LEFT, baseline), _BIG[0], fontsize=_BIG[1])
    small_x = _LEFT + pymupdf.get_text_length(_BIG[0], fontsize=_BIG[1])
    page.insert_text((small_x, baseline), _SMALL[0], fontsize=_SMALL[1], fontname="hebo")
    box = pymupdf.Rect(_BOX_X, baseline - 16, _BOX_X + 40, baseline - 10)
    page.draw_rect(box, color=None, fill=(0.9, 0.9, 0.9))


@pytest.fixture
def lines_doc(mine) -> dict:
    return upload(mine, _lines_page()).json()


def _texts_of(doc: dict) -> list[list[str]]:
    text_of = {span["id"]: span["text"] for span in doc["spans"]}
    return [[text_of[span_id] for span_id in line["span_ids"]] for line in doc["lines"]]


def _line_with(doc: dict, text: str) -> dict:
    text_of = {span["id"]: span["text"] for span in doc["spans"]}
    return next(line for line in doc["lines"] if text in map(text_of.get, line["span_ids"]))


def test_a_sentence_with_a_bold_word_is_one_line_of_its_three_spans(lines_doc):
    sentence = [text.strip() for text, _font in SENTENCE]

    lines = [[text.strip() for text in line] for line in _texts_of(lines_doc)]

    assert_equal(lines[0], sentence, "the sentence's spans, left to right")


def test_a_line_is_cut_only_where_moving_a_span_would_leave_something_behind(lines_doc):
    lines = [[text.strip() for text in line] for line in _texts_of(lines_doc)]

    kept_apart = {
        "a table's cells": _CELLS[0],
        "an underlined word": "terms",
        "a linked word": "site",
        "a raised letter": _SQUARED[1],
        "a word drawn twice": _TWICE,
        "the word after one drawn twice": _DUE.strip(),
    }
    for what, text in kept_apart.items():
        assert_equal([line for line in lines if text in line], [], what)
    assert_equal(["Please read", "the"] in lines, True, "the words before an underlined one")
    assert_equal(["Please see", "our"] in lines, True, "the words before a linked one")


def test_a_lines_room_is_the_least_any_of_its_spans_has_with_the_line_moving_as_one(lines_doc):
    sentence = _line_with(lines_doc, "spans")
    last = span_starting(lines_doc, 0, SENTENCE[2][0])
    far = span_starting(lines_doc, 0, "far")
    big_line = _line_with(lines_doc, _BIG[0])
    big = span_starting(lines_doc, 0, _BIG[0])
    small = span_starting(lines_doc, 0, _SMALL[0])

    assert_close(sentence["room_pt"], far["bbox"]["x0"] - last["bbox"]["x1"], 0.01, "a line")
    assert_close(big_line["room_pt"], _BOX_X - big["bbox"]["x1"], 0.01, "up to the box")
    assert_at_least(small["room_pt"], _BOX_X - small["bbox"]["x1"] + 1, "the small word's own")
