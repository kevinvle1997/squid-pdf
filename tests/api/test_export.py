"""Send edits, get the file back: every edit applied, and each redaction checked on it."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import Engine, words
from squidpdf.documents import store
from squidpdf.editing import constants as editing_constants
from tests.api.conftest import span_starting, upload
from tests.helpers import assert_equal, assert_in, assert_problem, assert_true

_SKIPPED = "Squid-Skipped-Edits"
_NOTICES = "Squid-Notices"
_HEADER = "CONFIDENTIAL"
_LINES = ["First page", "Second page", "Third page"]


class _InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run(self, _timeout, task):
        return task()


def _cannot_cut(_subsetter: Subsetter, _font: TTFont) -> None:
    """Fails, as fontTools can on an odd font."""
    raise ValueError("fontTools can't cut this font")


@pytest.fixture(scope="module")
def three_pages() -> bytes:
    """Three pages under the same header, each with its own line below it.

    The header repeats, so a redaction read on the wrong page finds it and fails.
    """
    doc = pymupdf.open()
    for line in _LINES:
        page = doc.new_page()
        page.insert_text((72, 72), _HEADER, fontname="helv", fontsize=10)
        page.insert_text((72, 100), line, fontname="helv", fontsize=12)
    return doc.tobytes()


@pytest.fixture
def three(mine, three_pages) -> dict:
    """The three pages as uploaded by `mine`."""
    return upload(mine, three_pages).json()


def _redact(span: dict) -> dict:
    """An edit redacting all of `span`."""
    return {"kind": "redact", "span_id": span["id"]}


def _export(
    client, doc: dict, edits: list[dict], pages: list[int] | None = None, language: str = "en"
):
    """Ask for the file with `edits` applied, as the browser does, reading `language`."""
    body = {"edits": edits} if pages is None else {"edits": edits, "pages": pages}
    headers = {"Accept-Language": language}
    return client.post(f"/api/documents/{doc['id']}/export", json=body, headers=headers)


def _opened(response) -> pymupdf.Document:
    """The downloaded file, opened; fails, showing the reply, if it isn't one."""
    kind = response.headers["content-type"]
    assert_equal((response.status_code, kind), (200, "application/pdf"), response.text[:200])
    return pymupdf.open(stream=response.content, filetype="pdf")


def _files(doc: dict) -> list[str]:
    """Everything in the document's folder, however deep."""
    folder = store.root() / doc["id"]
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def _lines(pdf: pymupdf.Document) -> list[list[str]]:
    """Each page's lines of text, in page order."""
    return [page.get_text().splitlines() for page in pdf.pages()]


def test_a_face_that_could_not_be_cut_down_is_said_in_a_header_and_the_file_still_comes(
    app, mine, doc, monkeypatch, pseudo
):
    """The file is only larger, but it's said, in the reader's words and in ASCII."""
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Subsetter, "subset", _cannot_cut)
    monkeypatch.setitem(words.CATALOGS[pseudo], "face_not_trimmed", "{font} ENTIÈRE")
    span = span_starting(doc, 0, "Made")  # its font is only named, so a face we ship redraws it
    edit = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [edit], language=pseudo)

    assert_in(span["text"], _lines(_opened(response))[0], "the redrawn page's lines")
    said = response.headers[_NOTICES]
    assert_true(said.isascii(), f"the header is ASCII: {said!r}")
    font = "Liberation Serif Regular"
    expected = [
        {
            "span_id": None,
            "detail": f"{font} ENTIÈRE",
            "edit": None,
            "code": "face_not_trimmed",
            "params": {"font": font},
        }
    ]
    assert_equal(json.loads(said), expected, "what came out other than asked")
    assert_equal(response.headers["Content-Language"], pseudo, "the language it's said in")


@pytest.mark.parametrize("pages", [None, [1, 0]], ids=["every page", "pages moved"])
def test_a_redaction_the_check_cannot_confirm_downloads_nothing(
    app, mine, doc, monkeypatch, pages
):
    """Text still in the saved file means no file, and the user is told which span.

    The erase is what's broken here, not the check: the text really is still
    in the file, and the check reads it where its page went.
    """
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patch below reaches it
    monkeypatch.setattr(Engine, "remove", lambda _engine, _spans: None)
    span = span_starting(doc, 1, "Invoices")
    kept = _files(doc)

    response = _export(mine, doc, [_redact(span)], pages=pages)

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=span["text"], page=2)
    assert_equal(response.json()["detail"], said, "what the user reads")
    assert_equal(_files(doc), kept, "the document's files after the export")


def test_what_an_export_saves_is_in_its_document_so_the_sweep_takes_it(
    app, mine, doc, monkeypatch
):
    """A worker killed mid-export can't clean up; what it saved must go with the document."""
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patch below reaches it
    saved: list[Path] = []
    save = Engine.save

    def noting_where(engine: Engine, path: str) -> list:
        saved.append(Path(path))
        return save(engine, path)

    monkeypatch.setattr(Engine, "save", noting_where)
    kept = _files(doc)

    _opened(_export(mine, doc, []))

    folder = store.root() / doc["id"]
    assert_equal(len(saved), 1, "files the export saved")
    assert_true(saved[0].is_relative_to(folder), f"{saved[0]} is in {folder}")
    assert_equal(_files(doc), kept, "the document's files after the export")


def test_pages_come_out_in_the_order_asked_with_redactions_read_where_they_went(mine, three):
    """Page 0's header is redacted and page 0 comes out second: it's read, and gone, there."""
    response = _export(mine, three, [_redact(span_starting(three, 0, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], ["First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def test_a_redaction_on_a_page_left_out_does_not_fail_the_export(mine, three):
    """The page goes, and its text with it: nothing is left to check."""
    response = _export(mine, three, [_redact(span_starting(three, 1, _HEADER))], pages=[2, 0])

    expected = [[_HEADER, "Third page"], [_HEADER, "First page"]]
    assert_equal(_lines(_opened(response)), expected, "each page's lines, in order")


def test_a_split_of_a_tagged_file_says_the_tags_went_with_the_pages_left_out(mine, tagged):
    """The tags point at every page, so leaving one out drops them: never silently."""
    doc = upload(mine, Path(tagged).read_bytes()).json()

    response = _export(mine, doc, [], pages=[0])

    said = json.loads(response.headers[_NOTICES])
    expected = [(None, "tags_dropped", words.sentence("tags_dropped"), None)]
    got = [(n["span_id"], n["code"], n["detail"], n["edit"]) for n in said]
    assert_equal(got, expected, "what came out other than asked")
    assert_equal(_opened(response).page_count, 1, "pages in the file")


def test_an_edit_pointing_at_nothing_is_skipped_and_named_in_the_header(mine, doc):
    """The body is the file, so what was left out travels beside it, by place in the list."""
    span = span_starting(doc, 1, "Invoices")
    ninety = span["text"].replace("thirty", "ninety")
    edits = [
        {"kind": "replace", "span_id": "nosuchspan00", "text": "x"},
        {"kind": "replace", "span_id": span["id"], "text": ninety},
        {"kind": "insert", "page": 7, "origin": [72, 700], "text": "Signed", "size": 12},
    ]

    response = _export(mine, doc, edits)

    assert_equal(response.headers[_SKIPPED], "0, 2", "edits left out")
    assert_in("ninety days", _opened(response)[1].get_text(), "the edit that was good")


def test_an_edit_after_a_redaction_on_the_same_text_fails_the_export(mine, doc):
    """Redaction wins: the list's order must not bring the text back into the file."""
    span = next(s for s in doc["spans"] if s["page"] == 1)
    redact = {"kind": "redact", "span_id": span["id"]}
    replace = {"kind": "replace", "span_id": span["id"], "text": span["text"]}

    response = _export(mine, doc, [redact, replace])

    assert_problem(response, "redaction_conflict", 422)


def test_a_redaction_pointing_at_nothing_fails_the_export(mine, doc):
    """Skipping it would send the text the user asked to remove."""
    response = _export(mine, doc, [{"kind": "redact", "span_id": "nosuchspan00"}])
    assert_problem(response, "bad_reference", 422)
    got = response.json()
    expected = ("bad_reference", {"span_id": "nosuchspan00"})
    assert_equal((got["code"], got["params"]), expected, "the problem, unsaid")


@pytest.mark.parametrize(
    ("pages", "problem", "status"),
    [
        ([2], "no_such_page", 422),
        ([0, 0], "invalid_request", 400),
        ([], "invalid_request", 400),
    ],
    ids=["past the last page", "a page twice", "no page at all: leave pages out for every one"],
)
def test_pages_the_document_cannot_give_are_a_problem_not_a_crash(
    mine, doc, pages, problem, status
):
    assert_problem(_export(mine, doc, [], pages), problem, status)


_LOWERED = 3  # each limit, lowered so a test can pass it with a short list


@pytest.mark.parametrize(
    ("limit", "made", "problem"),
    [
        ("MAX_EDITS", lambda span: [_redact(span)] * (_LOWERED + 1), "too_many_edits"),
        (
            "MAX_TEXT_CHARS",
            lambda span: [{"kind": "replace", "span_id": span["id"], "text": "x" * 4}],
            "text_too_long",
        ),
        (
            "MAX_TEXT_CHARS",
            lambda _span: [
                {"kind": "insert", "page": 0, "origin": [72, 700], "text": "xxxx", "size": 12}
            ],
            "text_too_long",
        ),
    ],
    ids=["too many edits", "a replacement too long", "an insert too long"],
)
def test_an_edit_list_past_a_limit_is_refused(mine, doc, monkeypatch, limit, made, problem):
    monkeypatch.setattr(editing_constants, limit, _LOWERED)
    edits = made(doc["spans"][0])
    assert_problem(_export(mine, doc, edits), problem, 422)
