"""The server's log: one line per request, by route, linked to its reply, and no document."""

from __future__ import annotations

import hashlib
from typing import get_args

import pymupdf

from squidpdf.core import Fidelity
from squidpdf.editing.export import _TALLY_OF  # noqa: PLC2701 (every fidelity has a tally)
from tests.api.conftest import InProcess, span_starting, upload
from tests.conftest import LOG_LINE, log_lines
from tests.helpers import assert_all, assert_equal, assert_in, assert_not_in

_HEADER = "Squid-Request-Id"
_UNLISTED_FONT = "WescottHouseSans"  # a company's own font: no list of ours names it


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


def test_a_request_that_runs_pdf_work_tallies_its_wait_and_its_work_on_its_line(
    browser, pdf_bytes, capsys
):
    """What it tallied in the worker comes back with its result, so a slow line says why."""
    mine = browser()
    capsys.readouterr()

    upload(mine, pdf_bytes)

    [line] = log_lines(capsys.readouterr().out)
    fields = dict(word.split("=") for word in line.split() if "=" in word)
    assert_in("queued_ms", fields, "the wait for a worker, on the upload's line")
    assert_in("worker_ms", fields, "the work in it, on the upload's line")


def _fields(line: str) -> dict[str, str]:
    """A line's fields, by name."""
    return dict(word.split("=", 1) for word in line.split() if "=" in word)


def test_an_export_counts_its_edits_by_whether_they_kept_their_font_and_names_each_stand_in(
    app, browser, pdf_bytes, capsys, monkeypatch
):
    """The share that kept their font is a sum over export lines; each similar one, a line."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    mine = browser()
    doc = upload(mine, pdf_bytes).json()
    delivery, made = span_starting(doc, 1, "Delivery"), span_starting(doc, 0, "Made")
    # The file's own font, and Times, which it only names, so a similar one draws.
    edits = [
        {"kind": "replace", "span_id": span["id"], "text": span["text"].replace("14", "2")}
        for span in (delivery, made)
    ]
    capsys.readouterr()

    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    assert_equal(exported.status_code, 200, "exported")
    lines = log_lines(capsys.readouterr().out)
    [access] = [line for line in lines if " request_done " in line]
    fields = _fields(access)
    counted = {name: fields.get(name) for name in ("edits_exact", "edits_substitute")}
    assert_equal(counted, {"edits_exact": "1", "edits_substitute": "1"}, "the export's counts")
    assert_not_in("edits_approximate", fields, "a count of none")
    [similar] = [line for line in lines if " edit_in_similar_font " in line]
    assert_equal(LOG_LINE.fullmatch(similar) is not None, True, f"{similar!r} has the shape")
    stood_in = _fields(similar)
    named = (stood_in["font"], stood_in["face"], stood_in["request"])
    expected = ("times", "LiberationSerif-Regular.ttf", fields["request"])
    assert_equal(named, expected, "the font, the face that drew it, and the export")
    assert_in("width_delta_pct", stood_in, "how far the line's width moved")


def _in_a_font_nobody_lists() -> bytes:
    """One line in a font the file only names, under a name none of our lists has.

    Drawn in Helvetica, then renamed, as a company's own font comes: its name is the file's.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Paid on 14 March 2026.", fontname="helv", fontsize=11)
    [(xref, *_rest)] = page.get_fonts()
    doc.xref_set_key(xref, "BaseFont", f"/{_UNLISTED_FONT}")
    return doc.tobytes()


def test_a_font_none_of_our_lists_name_is_logged_as_unlisted_never_by_its_name(
    app, browser, capsys, monkeypatch
):
    """A font's name can say whose file it is: only a name we list ourselves is logged."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    mine = browser()
    doc = upload(mine, _in_a_font_nobody_lists()).json()
    paid = span_starting(doc, 0, "Paid")
    edits = [{"kind": "replace", "span_id": paid["id"], "text": "Paid on 2 March 2026."}]
    capsys.readouterr()

    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    assert_equal(exported.status_code, 200, "exported")
    output = capsys.readouterr().out
    [similar] = [line for line in log_lines(output) if " edit_in_similar_font " in line]
    assert_equal(_fields(similar)["font"], "unlisted", "the font, which no list of ours names")
    assert_not_in(_UNLISTED_FONT, output, "the file's own name for its font")
    for text in (paid["text"], edits[0]["text"]):
        assert_not_in(text, output, "the line's words, before and after")


def test_every_fidelity_has_a_tally_on_the_exports_line():
    """One without would fail the export of the first edit that turned out so."""
    assert_equal(sorted(_TALLY_OF), sorted(get_args(Fidelity)), "the fidelities export counts")


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
