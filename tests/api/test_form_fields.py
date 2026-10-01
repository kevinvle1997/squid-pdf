"""Text a form field shows: an edit to it is said out loud, never silently not made."""

from __future__ import annotations

import pymupdf

from squidpdf.core import words
from tests.api.conftest import around, span_starting, upload
from tests.conftest import FORM_FIELD_VALUE
from tests.helpers import assert_equal, assert_problem

_SCALE = 2


def _said(rendered: dict) -> list[tuple[str, str, str, str]]:
    """Render's notices: each one's kind, its span, its sentence's key, and the sentence."""
    return [(n["kind"], n["span_id"], n["code"], n["detail"]) for n in rendered["notices"]]


def test_a_replace_in_a_form_field_is_left_as_it_was_and_said(mine, form):
    """Drawn over the field's own text, the new value would sit on the old one."""
    doc = upload(mine, form).json()
    field, line = span_starting(doc, 0, "SSN"), span_starting(doc, 0, "Name")
    edits = [
        {"kind": "replace", "span_id": field["id"], "text": "SSN on file"},
        {"kind": "replace", "span_id": line["id"], "text": "Name: Ada Lovelace"},
    ]
    body = {"edits": edits, "scale": _SCALE, "regions": [around(field), around(line)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    key = "form_field_not_edited"
    expected = [("span", field["id"], key, words.sentence(key))]
    assert_equal(_said(rendered), expected, "what render tells the user")
    lines = pymupdf.open(stream=exported.content, filetype="pdf")[0].get_text().splitlines()
    assert_equal(
        sorted(lines), sorted(["Name: Ada Lovelace", FORM_FIELD_VALUE]), "the file's lines"
    )


def test_a_redaction_in_a_form_field_is_warned_at_render_and_refused_at_export(mine, form):
    """The user hears it while they can still undo it, and the text never leaves in a file."""
    doc = upload(mine, form).json()
    field = span_starting(doc, 0, "SSN")
    edits = [{"kind": "redact", "span_id": field["id"]}]
    body = {"edits": edits, "scale": _SCALE, "regions": [around(field)]}

    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    key = "form_field_not_redacted"
    expected = [("span", field["id"], key, words.sentence(key))]
    assert_equal(_said(rendered), expected, "what render tells the user")
    assert_problem(exported, "redaction_failed", 422)
