"""Text a form field shows: an edit to it is said out loud, never silently not made."""

from __future__ import annotations

import pymupdf

from squidpdf.core import words
from tests.api.conftest import around, span_starting, upload
from tests.conftest import FORM_FIELD_VALUE, FORM_HINT, FORM_LINE
from tests.helpers import assert_equal

_SCALE = 2


def _said(rendered: dict) -> list[tuple[str, str, str, str]]:
    """Render's notices: each one's kind, its span, its sentence's key, and the sentence."""
    return [(n["kind"], n["span_id"], n["code"], n["detail"]) for n in rendered["notices"]]


def test_text_a_form_field_shows_is_marked_at_upload_before_any_edit(mine, form):
    """Rule 1: the browser can say it before the edit, not only once a render comes back."""
    doc = upload(mine, form).json()

    marked = {span["text"]: span["form_field"] for span in doc["spans"]}
    # The hint sits inside a field, but the page prints it: an edit reaches it.
    expected = {FORM_LINE: False, FORM_FIELD_VALUE: True, FORM_HINT: False}
    assert_equal(marked, expected, "spans a form field draws")
    key = "form_field_not_edited"
    assert_equal(doc["copy"][key], words.sentence(key), "what the browser says of one")


def test_a_replace_in_a_form_field_is_left_out_and_said_and_the_pages_own_text_is_not(
    mine, form
):
    """Drawn over the field's own text, the new value would sit on the old one.

    The hint the page prints inside an empty field is the page's: it's edited,
    as its fit said it would be.
    """
    doc = upload(mine, form).json()
    field, line = span_starting(doc, 0, "SSN"), span_starting(doc, 0, "Name")
    hint = span_starting(doc, 0, FORM_HINT)
    edits = [
        {"kind": "replace", "span_id": field["id"], "text": "SSN on file"},
        {"kind": "replace", "span_id": line["id"], "text": "Name: Ada Lovelace"},
        {"kind": "replace", "span_id": hint["id"], "text": "DD/MM/YYYY"},
    ]
    regions = [around(field), around(line), around(hint)]
    body = {"edits": edits, "scale": _SCALE, "regions": regions}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    key = "form_field_not_edited"
    expected = [("span", field["id"], key, words.sentence(key))]
    assert_equal(_said(rendered), expected, "what render tells the user")
    lines = pymupdf.open(stream=exported.content, filetype="pdf")[0].get_text().splitlines()
    expected_lines = ["Name: Ada Lovelace", FORM_FIELD_VALUE, "DD/MM/YYYY"]
    assert_equal(sorted(lines), sorted(expected_lines), "the file's lines")


def test_a_redaction_in_a_form_field_takes_its_words_out_of_the_field(mine, form):
    """A field's value is a copy of its words like any other: the redaction takes them out."""
    doc = upload(mine, form).json()
    field = span_starting(doc, 0, "SSN")
    edits = [{"kind": "redact", "span_id": field["id"]}]
    body = {"edits": edits, "scale": _SCALE, "regions": [around(field)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    assert_equal(_said(rendered), [], "what render tells the user")
    saved = pymupdf.open(stream=exported.content, filetype="pdf")
    assert_equal([widget.field_value for widget in saved[0].widgets()], ["", ""], "the values")
    assert_equal(saved[0].get_text().splitlines(), [FORM_LINE, FORM_HINT], "the page's lines")
