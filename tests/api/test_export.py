"""Send edits, get the file back: every edit applied, and each redaction checked on it."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pymupdf
import pytest
from fontTools.subset import Subsetter

from squidpdf.api.pool import WorkerPool
from squidpdf.core import Engine, words, write_dense
from squidpdf.documents import constants as documents_constants
from squidpdf.documents import store
from squidpdf.editing import Edit, export
from squidpdf.editing import constants as editing_constants
from squidpdf.editing.types import Exported
from tests.api.conftest import span_starting, upload
from tests.conftest import cannot_cut
from tests.helpers import assert_equal, assert_false, assert_in, assert_problem, assert_true

_SKIPPED = "Squid-Skipped-Edits"
_NOTICES = "Squid-Notices"
_HEADER = "CONFIDENTIAL"
_LINES = ["First page", "Second page", "Third page"]
_OPENING = "This agreement is made on"  # how the long fixture's first line starts
_UNTIMED_S = 600  # time enough for any analysis: an upload isn't what's timed here


class _InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run(self, _timeout, task):
        return task()


_STALL_S = 60  # far past any export timeout here
_STALLED_TIMEOUT_S = 3  # long enough to start the export, short enough to wait for
_make_pdf = export.make_pdf  # the real one, kept before a test swaps it


def _stall(*_args: object, **_kwargs: object) -> None:
    """Hangs, as MuPDF can on a hostile file."""
    time.sleep(_STALL_S)


def _make_pdf_stalling_once_open(
    folder: str, scratch: str, *, edits: list[Edit], pages: list[int] | None
) -> Exported:
    """`make_pdf`, hanging where it opens the original. Runs in a worker, the patch with it."""
    with mock.patch.object(store, "open_original", _stall):
        return _make_pdf(folder, scratch, edits=edits, pages=pages)


@pytest.fixture
def own_pool() -> Iterator[WorkerPool]:
    """Workers of the test's own, since it kills one; shut down after."""
    pool = WorkerPool()
    yield pool
    pool.close()


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
    monkeypatch.setattr(Subsetter, "subset", cannot_cut)
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
    monkeypatch.setattr(Engine, "remove", lambda _engine, _spans, then_drawn: None)
    span = span_starting(doc, 1, "Invoices")
    kept = _files(doc)

    response = _export(mine, doc, [_redact(span)], pages=pages)

    assert_problem(response, "redaction_failed", 422)
    said = words.sentence("redaction_failed").format(text=span["text"], page=2)
    assert_equal(response.json()["detail"], said, "what the user reads")
    assert_equal(_files(doc), kept, "the document's files after the export")


def test_a_document_deleted_while_its_export_runs_still_downloads(app, mine, doc, monkeypatch):
    """Its owner's DELETE, or the sweep, takes the folder once the original is open.

    The export used to save into that folder, so the save failed and the
    browser was told the file needs more memory.
    """
    monkeypatch.setattr(app.state, "pool", _InProcess())  # so the patch below reaches it
    opened = store.open_original

    def deleted_once_open(folder: Path, *args, **kwargs) -> Engine:
        engine = opened(folder, *args, **kwargs)
        store.delete(folder)
        return engine

    monkeypatch.setattr(store, "open_original", deleted_once_open)
    span = span_starting(doc, 1, "Invoices")
    ninety = span["text"].replace("thirty", "ninety")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": ninety}])

    assert_in("ninety days", _opened(response)[1].get_text(), "the downloaded page")
    assert_false((store.root() / doc["id"]).exists(), "the document's folder is still there")


def test_an_export_killed_at_its_timeout_leaves_nothing_in_the_temp_folder(
    app, mine, doc, monkeypatch, own_pool, tmp_path
):
    """The edited file is the user's, and nothing of a document is kept past the hour.

    A killed worker runs no cleanup of its own, so the server makes and removes
    the folder the export saves into.
    """
    monkeypatch.setenv(
        "TMPDIR", str(tmp_path)
    )  # the workers' temp folder, read when they start
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # and the server's
    monkeypatch.setattr(app.state, "pool", own_pool)
    monkeypatch.setattr(export, "make_pdf", _make_pdf_stalling_once_open)
    monkeypatch.setattr(export, "EXPORT_TIMEOUT_S", _STALLED_TIMEOUT_S)
    span = span_starting(doc, 1, "Invoices")

    response = _export(mine, doc, [{"kind": "replace", "span_id": span["id"], "text": "x"}])

    assert_problem(response, "too_slow", 503)
    assert_equal(sorted(tmp_path.iterdir()), [], "left in the temp folder")


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


@pytest.fixture(scope="module")
def longest(tmp_path_factory) -> bytes:
    """The longest document an upload takes: MAX_PAGES copies of one full contract page.

    Copied rather than drawn page by page, so it builds quickly; each copy is
    its own objects, as a real long file's pages are.
    """
    one_page = tmp_path_factory.mktemp("longest") / "one.pdf"
    write_dense(str(one_page), pages=1)
    longest = pymupdf.open()
    with pymupdf.open(one_page) as one:
        for _ in range(documents_constants.MAX_PAGES):
            longest.insert_pdf(one)
    return longest.tobytes()


def test_the_longest_document_exports_within_the_export_timeout(mine, longest, monkeypatch):
    """The whole way, through the API: #35 held only how a save grows with its pages."""
    monkeypatch.setattr(documents_constants, "ANALYSE_TIMEOUT_S", _UNTIMED_S)
    doc = upload(mine, longest).json()
    last_page = documents_constants.MAX_PAGES - 1
    line = span_starting(doc, last_page, _OPENING)
    edit = {"kind": "replace", "span_id": line["id"], "text": f"{_OPENING} 15 March 2026"}

    # Within EXPORT_TIMEOUT_S, or the pool stops it and the reply is too_slow.
    exported = _opened(_export(mine, doc, [edit]))

    assert_equal(exported.page_count, documents_constants.MAX_PAGES, "pages exported")
    assert_in(edit["text"], exported[last_page].get_text(), "the edit, on the last page")
