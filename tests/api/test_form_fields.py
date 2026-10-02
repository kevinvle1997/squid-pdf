"""Text a form field shows: an edit to it is said out loud, never silently not made."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import words
from tests.api.conftest import around, span_starting, upload
from tests.helpers import assert_equal, assert_problem

_SCALE = 2
_FIELD_VALUE = "SSN 078-05-1120"
_LINE = "Name: Ada Byron"
# PyMuPDF's PDF_WIDGET_TYPE_TEXT, a text field: set at import, so type checkers can't see it.
_TEXT_FIELD = 7


@pytest.fixture(scope="module")
def form() -> bytes:
    """One page: a line of the page's own text, and below it a filled-in text field.

    The field's value is drawn by the field itself (a widget), not by the page,
    so it's read as a span like any other, but erasing the page's text can't
    reach it. That's how a filled-in PDF form comes.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), _LINE, fontname="helv", fontsize=12)
    field = pymupdf.Widget()
    field.field_type = _TEXT_FIELD
    field.field_name = "ssn"
    field.field_value = _FIELD_VALUE
    field.rect = pymupdf.Rect(72, 120, 300, 140)
    field.text_fontsize = 12
    page.add_widget(field)
    return doc.tobytes()


def _said(rendered: dict) -> list[tuple[str | None, str, str]]:
    """Render's notices: the span each is about, its sentence's key, and the sentence."""
    return [(n["span_id"], n["code"], n["detail"]) for n in rendered["notices"]]


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
    expected = [(field["id"], key, words.sentence(key))]
    assert_equal(_said(rendered), expected, "what render tells the user")
    lines = pymupdf.open(stream=exported.content, filetype="pdf")[0].get_text().splitlines()
    assert_equal(
        sorted(lines), sorted(["Name: Ada Lovelace", _FIELD_VALUE]), "the file's lines"
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
    expected = [(field["id"], key, words.sentence(key))]
    assert_equal(_said(rendered), expected, "what render tells the user")
    assert_problem(exported, "redaction_failed", 422)
