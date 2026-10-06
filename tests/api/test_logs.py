"""The server's log: one line per request, by route, linked to its reply, and no document."""

from __future__ import annotations

import hashlib

from tests.api.conftest import span_starting, upload
from tests.conftest import LOG_LINE, log_lines
from tests.helpers import assert_all, assert_equal, assert_in, assert_not_in

_HEADER = "Squid-Request-Id"


def test_a_request_logs_one_line_by_its_route_naming_the_id_its_reply_carries(
    browser, pdf_bytes, capsys
):
    """The template, not the path, so a document's id never reaches the log."""
    mine = browser()
    doc = upload(mine, pdf_bytes).json()
    capsys.readouterr()

    answer = mine.get(f"/api/documents/{doc['id']}")

    [line] = log_lines(capsys.readouterr().out)
    request = answer.headers[_HEADER]
    assert_equal(LOG_LINE.fullmatch(line) is not None, True, f"{line!r} has the shape")
    expected = "noted   api.access request_done method=GET route=/api/documents/{doc_id}"
    assert_in(f" INFO  {expected} status=200 ms=", line, "the request's line")
    assert_equal(line.split()[-1], f"request={request}", "the id its reply carries")


def test_the_health_check_logs_nothing(server, capsys):
    """Docker asks every 30 s; a failing check shows in `docker ps` instead."""
    capsys.readouterr()
    server.get("/api/health")
    assert_equal(log_lines(capsys.readouterr().out), [], "lines for the health check")


def test_a_bug_logs_its_traceback_once_as_an_error_with_the_id_its_reply_carries(
    browser, capsys
):
    """The 500 says nothing of it; the id in its reply finds its lines in the log.

    A browser that raises what the app raises: a bug raised on past it, to be
    logged again by the server without its request, fails the test.
    """
    crashed = browser().get("/api/crash")

    output = capsys.readouterr().out
    request = crashed.headers[_HEADER]
    lines = log_lines(output)
    assert_all(lines, lambda line: line.endswith(f"request={request}"), repr)
    said = [" ".join(line.split()[1:5]) for line in lines]
    expected = [
        "ERROR noted api.access request_done",
        "ERROR failed api.errors.http unhandled",
    ]
    assert_equal(sorted(said), sorted(expected), "the request's lines")
    assert_equal(output.count("RuntimeError: a bug nobody caught"), 1, "tracebacks logged")


def test_nothing_logged_carries_a_document_its_words_or_its_owner(browser, pdf_bytes, capsys):
    """Upload, edit, export: the log says what happened, never what was in it or whose."""
    mine = browser()
    capsys.readouterr()
    doc = upload(mine, pdf_bytes).json()
    delivery = span_starting(doc, 1, "Delivery")
    typed = delivery["text"].replace("14 March", "2 March")
    edits = [{"kind": "replace", "span_id": delivery["id"], "text": typed}]
    rendered = mine.post(
        f"/api/documents/{doc['id']}/render", json={"edits": edits, "scale": 2, "regions": []}
    )
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal((rendered.status_code, exported.status_code), (200, 200), "edited, exported")

    output = capsys.readouterr().out
    owner = mine.cookies["__Host-owner"]
    private = {
        "the document's id": doc["id"],
        "the edit's text": typed,
        "the owner token": owner,
        "the owner token's hash": hashlib.sha256(owner.encode()).hexdigest(),
        **{f"span {span['id']}'s text": span["text"] for span in doc["spans"]},
    }
    assert_equal(len(log_lines(output)), 3, "a line per request")
    for what, value in private.items():
        assert_not_in(value, output, what)
