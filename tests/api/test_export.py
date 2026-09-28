"""Send edits, get the file back: every edit applied, and each redaction checked on it."""

from __future__ import annotations

import tempfile

import pymupdf
import pytest

from squidpdf.core import Engine, words
from squidpdf.editing.constants import MAX_TEXT_CHARS
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_problem

_SKIPPED = "Squid-Skipped-Edits"
_HEADER = "CONFIDENTIAL"
_LINES = ["First page", "Second page", "Third page"]


class _InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run(self, _timeout, fn, /, *args, **kwargs):
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def three_pages() -> bytes:
    """Three pages under the same header, each with its own line below it.

    The header is in the same place on every page, so a redaction read on the
    wrong page finds its text there, and the export fails.
    """
    doc = pymupdf.open()
    for line in _LINES:
        page = doc.new_page()
        page.insert_text((72, 72), _HEADER, fontname="helv", fontsize=10)
        page.insert_text((72, 100), line, fontname="helv", fontsize=12)
    return doc.tobytes()


@pytest.fixture
def mine(browser):
    """The browser that uploads, and so owns, the documents."""
    return browser()


@pytest.fixture
def doc(mine, pdf_bytes) -> dict:
    """The two-page sample as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


@pytest.fixture
def three(mine, three_pages) -> dict:
    """The three pages as uploaded by `mine`."""
    return upload(mine, three_pages).json()


def _span(doc: dict, page: int, starts: str) -> dict:
    """The span on `page` whose text starts with `starts`."""
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def _redact(span: dict) -> dict:
    """An edit redacting all of `span`."""
    return {"kind": "redact", "span_id": span["id"]}


def _export(client, doc: dict, edits: list[dict], pages: list[int] | None = None):
    """Ask for the file with `edits` applied, as the browser does."""
    body = {"edits": edits} if pages is None else {"edits": edits, "pages": pages}
    return client.post(f"/api/documents/{doc['id']}/export", json=body)


def _opened(response) -> pymupdf.Document:
    """The downloaded file, opened; fails, showing the reply, if it isn't one."""
    kind = response.headers["content-type"]
    assert_equal((response.status_code, kind), (200, "application/pdf"), response.text[:200])
    return pymupdf.open(stream=response.content, filetype="pdf")


def _lines(pdf: pymupdf.Document) -> list[list[str]]:
    """Each page's lines of text, in page order."""
    return [page.get_text().splitlines() for page in pdf.pages()]


def test_an_export_opens_and_a_replaced_span_reads_back_as_the_new_text(mine, doc):
    span = _span(doc, 1, "Delivery")
    text = span["text"].replace("14 March", "2 March")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": text}])

    pdf = _opened(response)
    assert_equal(pdf.page_count, 2, "pages in the file")
    assert_in(text, _lines(pdf)[1], "the edited page's lines")
    assert_not_in("14 March", pdf[1].get_text(), "the edited page")
    assert_equal(response.headers[_SKIPPED], "", "edits left out")


def test_a_redacted_span_is_gone_from_the_downloaded_file(mine, doc):
    span = _span(doc, 1, "Invoices")

    pdf = _opened(_export(mine, doc, [_redact(span)]))

    text = "".join(page.get_text() for page in pdf.pages())
    assert_not_in(span["text"], text, "the file's text")
    assert_in("Delivery begins 14 March", text, "the file's text")


def test_a_redaction_the_check_cannot_confirm_downloads_nothing(
    app, mine, doc, monkeypatch, tmp_path
):
    """Rule 4: text still in the saved file means no file, and the user is told which."""
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patches below reach it
    monkeypatch.setattr(Engine, "absent", lambda _engine, _span: False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    span = _span(doc, 1, "Invoices")

    response = _export(mine, doc, [_redact(span)])

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=span["text"], page=2)
    assert_equal(response.json()["detail"], said, "what the user reads")
    assert_equal(list(tmp_path.iterdir()), [], "files the export left behind")


def test_pages_come_out_in_the_order_asked_with_redactions_read_where_they_went(mine, three):
    """Page 0's header is redacted and page 0 comes out second: it's read, and gone, there."""
    response = _export(mine, three, [_redact(_span(three, 0, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], ["First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def test_a_redaction_on_a_page_left_out_does_not_fail_the_export(mine, three):
    """The page goes, and its text with it: nothing is left to check."""
    response = _export(mine, three, [_redact(_span(three, 1, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], [_HEADER, "First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def test_an_edit_pointing_at_nothing_is_skipped_and_named_in_the_header(mine, doc):
    """The body is the file, so what was left out travels beside it, by place in the list."""
    span = _span(doc, 1, "Invoices")
    ninety = span["text"].replace("thirty", "ninety")
    edits = [
        {"kind": "replace", "span_id": "nosuchspan00", "text": "x"},
        {"kind": "replace", "span_id": span["id"], "text": ninety},
        {"kind": "insert", "page": 7, "origin": [72, 700], "text": "Signed", "size": 12},
    ]

    response = _export(mine, doc, edits)

    assert_equal(response.headers[_SKIPPED], "0, 2", "edits left out")
    assert_in("ninety days", _opened(response)[1].get_text(), "the edit that was good")


def test_a_redaction_pointing_at_nothing_fails_the_export(mine, doc):
    """Skipping it would send the text the user asked to remove."""
    response = _export(mine, doc, [{"kind": "redact", "span_id": "nosuchspan00"}])
    assert_problem(response, "bad_reference", 422)


@pytest.mark.parametrize(
    ("pages", "problem", "status"),
    [
        ([2], "no_such_page", 422),
        ([-1], "no_such_page", 422),
        ([0, 0], "invalid_request", 400),
        ([], "invalid_request", 400),
    ],
    ids=["past the last page", "before the first", "a page twice", "no pages"],
)
def test_pages_the_document_cannot_give_are_a_problem_not_a_crash(
    mine, doc, pages, problem, status
):
    assert_problem(_export(mine, doc, [], pages), problem, status)


def test_new_text_past_the_limit_is_refused(mine, doc):
    text = "x" * (MAX_TEXT_CHARS + 1)
    edit = {"kind": "replace", "span_id": doc["spans"][0]["id"], "text": text}
    assert_problem(_export(mine, doc, [edit]), "text_too_long", 422)


def test_another_browser_is_told_there_is_no_such_document(browser, doc, mine):
    assert_equal(_export(mine, doc, []).status_code, 200, "the owner's status")
    assert_problem(_export(browser(), doc, []), "not_found", 404)
