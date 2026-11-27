from __future__ import annotations

import pymupdf
import pytest

from tests.api.conftest import around, render, span_starting, upload
from tests.conftest import SENTENCE, each_letter, each_span, write_runs
from tests.helpers import (
    assert_at_most,
    assert_close,
    assert_equal,
    assert_in,
    assert_problem,
    assert_true,
)

_SIZE = 10
_LEFT = 72
_SENTENCE_AT = (_LEFT, 100)
_BILL = (("Please pay the full ", "helv"), ("total", "hebo"), (" today.", "helv"))
_BILL_AT = (_LEFT, 160)
_RULE_GAP = 6
_FAR = (480, 400)
_MOVED = 0.05  # a run further than this from where it was has moved: MOVED_PT


def _lines_page() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    write_runs(page, SENTENCE, origin=_SENTENCE_AT, size=_SIZE)
    bill_end = write_runs(page, _BILL, origin=_BILL_AT, size=_SIZE)[-1]
    rule_x = bill_end + _RULE_GAP
    page.draw_line((rule_x, _BILL_AT[1] - _SIZE), (rule_x, _BILL_AT[1] + 2), width=0.5)
    page.insert_text(_FAR, "far", fontsize=_SIZE)
    return doc.tobytes()


@pytest.fixture
def lines_doc(mine) -> dict:
    return upload(mine, _lines_page()).json()


def _line_of(doc: dict, text: str) -> list[dict]:
    by_id = {span["id"]: span for span in doc["spans"]}
    line = next(
        line
        for line in doc["lines"]
        if any(by_id[span_id]["text"].startswith(text) for span_id in line["span_ids"])
    )
    return [by_id[span_id] for span_id in line["span_ids"]]


def _typed(spans: list[dict], texts: list[str], strategy: str = "as-is") -> dict:
    runs = [
        {"span_id": span["id"], "text": text} for span, text in zip(spans, texts, strict=True)
    ]
    return {"kind": "replace_runs", "runs": runs, "strategy": strategy}


def _exported(client, doc: dict, edits: list[dict]) -> list[dict]:
    response = client.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal(response.status_code, 200, "exported")
    page = pymupdf.open(stream=response.content, filetype="pdf")[0]
    return list(each_span(page.get_text("dict")["blocks"]))


def _drawn(spans: list[dict], text: str) -> dict:
    return next(span for span in spans if span["text"].strip() == text.strip())


def test_a_line_edited_as_one_keeps_each_runs_font_and_moves_the_runs_after_the_change(
    mine, lines_doc
):
    spans = _line_of(lines_doc, "Text")
    edit = _typed(spans, ["Text is now grouped into ", "spans", " (one style)."])
    fits = render(mine, lines_doc, [edit], [around(spans[0])]).json()["fits"]

    saved = _exported(mine, lines_doc, [edit])

    delta = fits[spans[0]["id"]]["delta_pt"]
    for span in spans[1:]:
        drawn = _drawn(saved, span["text"])
        moved_by = drawn["origin"][0] - span["origin"][0]
        assert_close(moved_by, delta, _MOVED, f"how far {span['text']!r} moved")
    assert_in("Bold", _drawn(saved, "spans")["font"], "the bold word's font, still bold")
    plain = [
        _drawn(saved, text)["font"] for text in ("Text is now grouped into", "(one style).")
    ]
    assert_equal(["Bold" in font for font in plain], [False, False], "the rest, still plain")


def test_the_runs_before_a_change_are_left_where_the_page_drew_them(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    edit = _typed(spans, ["Text is grouped into ", "spans", " (one style, one baseline)."])

    saved = _exported(mine, lines_doc, [edit])

    for span in spans[:2]:
        drawn = _drawn(saved, span["text"])
        assert_close(drawn["origin"][0], span["origin"][0], 0.01, f"where {span['text']!r} is")


def test_a_line_typed_past_its_room_says_so_with_its_ways_out_for_every_run_redrawn(
    mine, lines_doc
):
    spans = _line_of(lines_doc, "Please")
    edit = _typed(spans, ["Please pay the fuller ", "total", " today."])

    fits = render(mine, lines_doc, [edit], [around(spans[0])]).json()["fits"]

    assert_equal(sorted(fits), sorted(span["id"] for span in spans), "a fit per run redrawn")
    first = fits[spans[0]["id"]]
    assert_equal(first["options"], ["shrink", "as-is"], "the ways out")
    assert_in("too long", first["message"], "what's said of it")
    shared = {fit["delta_pt"] for fit in fits.values()}
    assert_equal(shared, {first["delta_pt"]}, "one length, the line's, on every run")


def test_a_line_shrunk_to_fit_leaves_the_runs_after_its_change_where_they_were(mine, lines_doc):
    spans = _line_of(lines_doc, "Please")
    edit = _typed(spans, ["Please pay the fuller ", "total", " today."], strategy="shrink")

    saved = _exported(mine, lines_doc, [edit])

    changed = _drawn(saved, "Please pay the fuller")
    assert_true(changed["size"] < _SIZE, f"the changed run drawn smaller: {changed['size']}")
    for span in spans[1:]:
        drawn = _drawn(saved, span["text"])
        assert_close(
            drawn["origin"][0], span["origin"][0], _MOVED, f"where {span['text']!r} is"
        )
    assert_in("Bold", _drawn(saved, "total")["font"], "the bold word, still bold")


def test_an_edit_naming_spans_that_are_not_one_line_is_left_out_saying_so(mine, lines_doc):
    first = span_starting(lines_doc, 0, "Text")
    other = span_starting(lines_doc, 0, "Please")
    edit = _typed([first, other], ["Text", "Please"])

    rendered = render(mine, lines_doc, [edit], [around(first)]).json()

    assert_equal([skip["code"] for skip in rendered["skipped"]], ["not_one_line"], "left out")


def test_a_redaction_of_one_run_after_its_lines_edit_takes_the_whole_edit(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    edit = _typed(spans, ["Text is now grouped into ", "spans", " (one style)."])
    redact = {"kind": "redact", "span_id": spans[1]["id"]}

    saved = _exported(mine, lines_doc, [edit, redact])

    texts = [span["text"].strip() for span in saved]
    assert_in("Text is grouped into", texts, "the line as it was, but for the redaction")
    assert_equal("spans" in texts, False, "the word redacted")


def test_a_line_edit_beside_a_redaction_on_another_line_downloads_checked(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    edit = _typed(spans, ["Text is now grouped into ", "spans", " (one style)."])
    total = span_starting(lines_doc, 0, "total")
    redact = {"kind": "redact", "span_id": total["id"]}

    saved = _exported(mine, lines_doc, [redact, edit])

    texts = [span["text"].strip() for span in saved]
    assert_in("Text is now grouped into", texts, "the line edited")
    assert_equal("total" in texts, False, "the word redacted on the other line")


def test_a_line_edit_over_a_run_redacted_before_it_is_refused(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    redact = {"kind": "redact", "span_id": spans[1]["id"]}
    edit = _typed(spans, ["Text is now grouped into ", "spans", " (one style)."])

    response = render(mine, lines_doc, [redact, edit], [around(spans[0])])

    assert_problem(response, "redaction_conflict", 422)


def test_a_line_emptied_whole_is_a_bad_request(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")

    response = render(mine, lines_doc, [_typed(spans, ["", "", ""])], [around(spans[0])])

    assert_problem(response, "invalid_request", 400)


def _saved_letters(client, doc: dict, edits: list[dict]) -> list[dict]:
    """Each letter the saved file draws on its page, with where, in reading order."""
    response = client.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal(response.status_code, 200, "exported")
    page = pymupdf.open(stream=response.content, filetype="pdf")[0]
    return list(each_letter(page.get_text("rawdict")["blocks"]))


def _letter_x(letters: list[dict], words: str, *, baseline: float) -> float:
    """Where `words` starts on the line at `baseline`, as the saved file draws it."""
    line = sorted(
        (letter for letter in letters if abs(letter["origin"][1] - baseline) < 1),
        key=lambda letter: letter["origin"][0],
    )
    at = "".join(letter["c"] for letter in line).index(words)
    return line[at]["origin"][0]


def _page_x(span: dict, before: str) -> float:
    """Where the page draws what follows `before` in `span`, set in plain Helvetica."""
    return span["origin"][0] + pymupdf.get_text_length(before, "helv", _SIZE)


def _part(span: dict, text: str, **cut: int) -> dict:
    """An edit of part of one span, from `start` to `end`, typed as `text`."""
    return {"kind": "replace_runs", "runs": [{"span_id": span["id"], "text": text, **cut}]}


def test_text_after_a_split_inside_a_span_stays_where_the_page_drew_it(mine, lines_doc):
    first = _line_of(lines_doc, "Text")[0]
    edit = _part(first, "Text a ", end=len("Text is "))

    letters = _saved_letters(mine, lines_doc, [edit])

    baseline = first["origin"][1]
    after = _letter_x(letters, "grouped", baseline=baseline)
    assert_close(after, _page_x(first, "Text is "), _MOVED, "the words past the split")


def test_a_split_lets_an_edit_grow_only_into_the_gap_before_what_follows(mine, lines_doc):
    """A line's end may run a little past its room; text right after a split may not."""
    first = _line_of(lines_doc, "Text")[0]
    edit = _part(first, "Text is i ", end=len("Text is "))

    fits = render(mine, lines_doc, [edit], [around(first)]).json()["fits"]

    fit = fits[first["id"]]
    assert_at_most(fit["delta_pt"], 5, "within the tolerance a line's end has")
    assert_in("too long", fit["message"], "but past the gap, so said")


def test_an_edit_after_a_split_is_set_from_where_the_page_drew_it(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    start = len("Text is ")
    runs = [
        {"span_id": spans[0]["id"], "text": "gathered into ", "start": start},
        {"span_id": spans[1]["id"], "text": "spans"},
        {"span_id": spans[2]["id"], "text": spans[2]["text"]},
    ]
    edit = {"kind": "replace_runs", "runs": runs}

    rendered = render(mine, lines_doc, [edit], [around(spans[0])]).json()
    letters = _saved_letters(mine, lines_doc, [edit])

    baseline = spans[0]["origin"][1]
    assert_close(
        _letter_x(letters, "gathered", baseline=baseline),
        _page_x(spans[0], "Text is "),
        _MOVED,
        "the edit starts at the split",
    )
    assert_close(
        _letter_x(letters, "Text", baseline=baseline),
        spans[0]["origin"][0],
        _MOVED,
        "before",
    )
    assert_in(f"{spans[0]['id']}@{start}", rendered["fits"], "its fit, kept by where it starts")


def test_edits_on_both_sides_of_a_split_both_stand(mine, lines_doc):
    first = _line_of(lines_doc, "Text")[0]
    split = len("Text is ")
    before = _part(first, "Test is ", end=split)
    after = {**_part(first, "gathered into ", start=split)}

    letters = _saved_letters(mine, lines_doc, [before, after])

    baseline = first["origin"][1]
    assert_close(_letter_x(letters, "Test", baseline=baseline), first["origin"][0], 0.05, "one")
    assert_close(
        _letter_x(letters, "gathered", baseline=baseline),
        _page_x(first, "Text is "),
        _MOVED,
        "the other, from the split",
    )


def test_a_shrunk_edit_before_a_split_between_runs_leaves_the_next_where_it_was(
    mine, lines_doc
):
    spans = _line_of(lines_doc, "Text")
    edit = {**_part(spans[0], "Text is grouped intoo "), "strategy": "shrink"}

    saved = _exported(mine, lines_doc, [edit])

    changed = _drawn(saved, "Text is grouped intoo")
    assert_true(changed["size"] < _SIZE, f"drawn smaller: {changed['size']}")
    bold = _drawn(saved, "spans")
    assert_close(bold["origin"][0], spans[1]["origin"][0], 0.01, "the bold word stays")


def test_a_span_on_no_line_is_split_in_its_own_room(mine, lines_doc):
    far = span_starting(lines_doc, 0, "far")
    edit = _part(far, "f", end=1)

    letters = _saved_letters(mine, lines_doc, [edit])

    baseline = far["origin"][1]
    assert_close(
        _letter_x(letters, "ar", baseline=baseline), _page_x(far, "f"), _MOVED, "the rest"
    )


@pytest.mark.parametrize(
    "cut",
    [{"end": 500}, {"start": 30}, {"start": 4, "end": 4}, {"start": -1}],
    ids=["past the text", "starting past it", "nothing", "before it"],
)
def test_a_split_where_a_span_has_no_text_is_a_bad_request(mine, lines_doc, cut):
    first = _line_of(lines_doc, "Text")[0]

    response = render(mine, lines_doc, [_part(first, "x", **cut)], [around(first)])

    assert_problem(response, "invalid_request", 400)


def test_a_split_anywhere_but_an_edits_two_ends_is_a_bad_request(mine, lines_doc):
    spans = _line_of(lines_doc, "Text")
    edit = _typed(spans, ["Text is grouped into ", "span", " (one style)."])
    edit["runs"][1]["start"] = 1

    response = render(mine, lines_doc, [edit], [around(spans[0])])

    assert_problem(response, "invalid_request", 400)
