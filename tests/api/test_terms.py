"""The service's terms come from the file its deployment names; a copy naming none has none."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.core import Message, words
from squidpdf.terms.terms_file import TermsUnreadable
from tests.api.conftest import BASE_URL
from tests.helpers import assert_equal

# The terms CI's deploy serves (.github/workflows/ci.yml): placeholder words in every part
# the page shows, a paragraph wrapped over two lines, and an email address.
_FIXTURE = Path(__file__).parents[2] / "fixtures" / "terms.md"
_SERVED = {
    "title": "Example terms",
    "last_changed": "Last changed 1 January 2000",
    "paragraphs": [
        {
            "lead": "First lead.",
            "pieces": [
                {
                    "kind": "words",
                    "text": "The first paragraph, which wraps onto a second line.",
                }
            ],
        },
        {
            "lead": "Second lead.",
            "pieces": [
                {"kind": "words", "text": "Write to "},
                {"kind": "email", "address": "someone@example.com"},
                {"kind": "words", "text": " about anything."},
            ],
        },
    ],
}


def _terms_answered(monkeypatch, named: str | None):
    """What GET /api/terms answers on an app started with `SQUIDPDF_TERMS` set to `named`."""
    if named is None:
        monkeypatch.delenv("SQUIDPDF_TERMS", raising=False)
    else:
        monkeypatch.setenv("SQUIDPDF_TERMS", named)
    # Its lifespan isn't run: the terms need no workers.
    return TestClient(create_app(), base_url=BASE_URL).get("/api/terms")


@pytest.mark.parametrize("saved", ["as written", "as Notepad saves it"])
def test_the_terms_are_sent_as_the_deployment_wrote_them(monkeypatch, tmp_path, saved):
    """Notepad starts the file with a byte-order mark and ends each line with CR LF."""
    written = _FIXTURE.read_text(encoding="utf-8")
    path = tmp_path / "terms.md"
    if saved == "as written":
        path.write_text(written, encoding="utf-8")
    else:
        path.write_text(written, encoding="utf-8-sig", newline="\r\n")

    response = _terms_answered(monkeypatch, str(path))

    assert_equal(response.status_code, 200, "status of the terms")
    assert_equal(response.json(), _SERVED, "the terms, each paragraph's lines joined")


@pytest.mark.parametrize("named", [None, ""])
def test_a_deployment_naming_no_terms_has_none(monkeypatch, named):
    """A self-hosted copy sets nothing; compose passes an empty value for an unset one."""
    response = _terms_answered(monkeypatch, named)

    assert_equal(response.status_code, 200, "status of no terms")
    assert_equal(response.json(), None, "no terms")


# Each file the page can't show as written, and the sentence and line it's refused with.
_REFUSED = {
    "no title": ("Example terms\n\n**A.** x\n", "terms_no_title", 1),
    "empty": ("", "terms_no_title", 1),
    "a third line under the title": (
        "# T\nLast changed today\nby us\n\n**A.** x\n",
        "terms_title_too_long",
        3,
    ),
    "no paragraph": ("# T\n", "terms_no_paragraphs", 1),
    "a paragraph with no lead": ("# T\n\nNo lead.\n", "terms_no_lead", 3),
    "an empty lead": ("# T\n\n** ** x\n", "terms_empty_lead", 3),
    "a lead alone": ("# T\n\n**A.**\n", "terms_lead_alone", 3),
    "bold past the lead": ("# T\n\n**A.** You must **not**.\n", "terms_emphasis", 3),
    "italic": ("# T\n\n**A.** x\n_quietly_ y\n", "terms_emphasis", 4),
    "a list": ("# T\n\n**A.** You may not:\n- forge\n", "terms_list", 4),
    "a numbered list": ("# T\n\n**A.** You may not:\n1. forge\n", "terms_list", 4),
    "a link": ("# T\n\n**A.** Write to [me](mailto:a@b.c).\n", "terms_link", 3),
    "a link in brackets": ("# T\n\n**A.** See <https://x.y>.\n", "terms_link", 3),
    "HTML": ("# T\n\n**A.** <b>x</b>\n", "terms_html", 3),
    "a heading in a paragraph": ("# T\n\n**A.** x\n## More\n", "terms_heading", 4),
    "a bold title, as the draft's": (
        "# **T** · last changed [date]\n\n**A.** x\n",
        "terms_title_formatted",
        1,
    ),
    "a paragraph with no blank line above it": (
        "# T\n**A.** x\n",
        "terms_paragraph_under_title",
        2,
    ),
    "a heading under the title": ("# T\n## Sub\n\n**A.** x\n", "terms_date_formatted", 2),
    "a list under the title": ("# T\n- item\n\n**A.** x\n", "terms_date_formatted", 2),
    "a quotation": ("# T\n\n**A.** x\n> quoted\n", "terms_quote", 4),
    "a line of -": ("# T\n\n**A.** x\n---\n", "terms_rule", 4),
    "a line of =": ("# T\n\n**A.** x\n===\n", "terms_rule", 4),
    "a table": ("# T\n\n**A.** x\n| a | b |\n", "terms_table", 4),
    "struck through": ("# T\n\n**A.** ~~old~~ new\n", "terms_emphasis", 3),
    "an HTML entity": ("# T\n\n**A.** a&nbsp;b\n", "terms_entity", 3),
    "an escaped character": ("# T\n\n**A.** 1\\. x\n", "terms_escape", 3),
}


@pytest.mark.parametrize(("written", "key", "line"), _REFUSED.values(), ids=_REFUSED.keys())
def test_terms_the_page_cant_show_stop_the_app_starting(
    monkeypatch, tmp_path, written, key, line
):
    """Never shown as raw Markdown: refused, naming the file and the line."""
    path = tmp_path / "terms.md"
    path.write_text(written, encoding="utf-8")
    monkeypatch.setenv("SQUIDPDF_TERMS", str(path))

    with pytest.raises(TermsUnreadable) as refused:
        create_app()

    expected = words.render(Message(key, {"path": str(path), "line": line}))
    assert_equal(str(refused.value), expected, "why the terms are refused")


def test_a_lead_said_twice_stops_the_app_starting(monkeypatch, tmp_path):
    path = tmp_path / "terms.md"
    path.write_text("# T\n\n**A.** x\n\n**A.** y\n", encoding="utf-8")
    monkeypatch.setenv("SQUIDPDF_TERMS", str(path))

    with pytest.raises(TermsUnreadable) as refused:
        create_app()

    said = Message("terms_repeated_lead", {"path": str(path), "line": 5, "lead": "A."})
    expected = words.render(said)
    assert_equal(str(refused.value), expected, "why the terms are refused")


@pytest.mark.parametrize("unread", ["missing", "a folder", "not UTF-8"])
def test_a_terms_file_that_cant_be_read_stops_the_app_starting(monkeypatch, tmp_path, unread):
    path = tmp_path / "terms.md"
    if unread == "a folder":
        path.mkdir()
    if unread == "not UTF-8":
        path.write_bytes("# T\n\n**Who.** \xe9t\xe9\n".encode("latin-1"))
    monkeypatch.setenv("SQUIDPDF_TERMS", str(path))

    with pytest.raises(TermsUnreadable) as refused:
        create_app()

    expected = {
        "missing": Message("terms_missing", {"path": str(path)}),
        "a folder": Message("terms_folder", {"path": str(path)}),
        "not UTF-8": Message("terms_not_utf8", {"path": str(path), "line": 3}),
    }[unread]
    assert_equal(str(refused.value), words.render(expected), "why the terms can't be read")
