"""The server's log: one line per request, by route, linked to its reply, and no document."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import get_args

import pymupdf

from squidpdf.core import FACES, Fidelity
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.documents.store import FolderStore
from squidpdf.editing.export import _TALLY_OF  # noqa: PLC2701 (every fidelity has a tally)
from tests.api.conftest import (
    InProcess,
    edit_counts,
    export_lines,
    log_fields,
    refusing_own_copies,
    span_starting,
    upload,
)
from tests.conftest import LOG_LINE, log_lines, named_with_widths
from tests.helpers import (
    assert_all,
    assert_close,
    assert_equal,
    assert_in,
    assert_not_in,
    assert_true,
)

_HEADER = "Squid-Request-Id"
_UNLISTED = "WescottHouseSans"  # a company's own font: no list of ours names it
_SANS = "Liberation Sans Regular"  # the face that draws a sans no list of ours names


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
    fields = log_fields(line)
    assert_in("queued_ms", fields, "the wait for a worker, on the upload's line")
    assert_in("worker_ms", fields, "the work in it, on the upload's line")


def _substitute_lines(lines: list[str]) -> list[dict[str, str]]:
    """The fields of each line for an edit a similar font drew."""
    return [log_fields(line) for line in lines if " edit_in_substitute " in line]


def _replaced(span: dict, text: str) -> dict:
    """A replace of `span`'s text with `text`."""
    return {"kind": "replace", "span_id": span["id"], "text": text}


def test_an_export_counts_its_edits_by_whether_they_kept_their_font_and_names_each_substitute(
    app, browser, pdf_bytes, capsys, monkeypatch
):
    """The share that kept their font is a sum over export lines; each similar one, a line."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    mine = browser()
    doc = upload(mine, pdf_bytes).json()
    delivery, made = span_starting(doc, 1, "Delivery"), span_starting(doc, 0, "Made")
    invoices = span_starting(doc, 1, "Invoices")
    # The file's own font; Times, which it only names; and a span's own words typed back.
    edits = [
        *(_replaced(span, span["text"].replace("14", "2")) for span in (delivery, made)),
        _replaced(invoices, invoices["text"]),
    ]

    lines = export_lines(mine, doc, edits, capsys)

    expected = {"edits_exact": "1", "edits_substitute": "1"}
    assert_equal(edit_counts(lines), expected, "the export's counts")
    [substitute_line] = [line for line in lines if " edit_in_substitute " in line]
    assert_true(LOG_LINE.fullmatch(substitute_line) is not None, f"{substitute_line!r}'s shape")
    [request] = {line.split()[-1] for line in lines}
    fields = log_fields(substitute_line)
    named = {name: fields[name] for name in ("font", "face", "same_widths", "width_delta_pct")}
    # Liberation Serif has Times's widths: nothing on the line moves, whatever was typed.
    widths = {"same_widths": "true", "width_delta_pct": "0.0"}
    expected_fields = {"font": "times", "face": "LiberationSerif-Regular.ttf", **widths}
    assert_equal(named, expected_fields, "the font, the face that drew it, and its widths")
    assert_equal(f"request={fields['request']}", request, "the export's request")


def test_a_replace_the_page_couldnt_take_counts_as_it_was_drawn(
    app, browser, pdf_bytes, form, capsys, monkeypatch
):
    """A field's value isn't drawn, so not counted; a font the page refuses is a substitute."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    mine = browser()
    filled = upload(mine, form).json()
    value = span_starting(filled, 0, "SSN")

    stuck = export_lines(mine, filled, [_replaced(value, "SSN 000-00-0000")], capsys)

    assert_equal(edit_counts(stuck), {}, "the counts for an edit the field kept from the page")
    assert_equal(_substitute_lines(stuck), [], "lines for it")

    monkeypatch.setattr(app.state, "documents", FolderStore(refusing_own_copies))
    doc = upload(mine, pdf_bytes).json()
    delivery = span_starting(doc, 1, "Delivery")

    refused = export_lines(mine, doc, [_replaced(delivery, "Delivery begins 2")], capsys)

    assert_equal(edit_counts(refused), {"edits_substitute": "1"}, "the counts, as it was drawn")
    [fields] = _substitute_lines(refused)
    assert_equal(fields["face"], "LiberationSerif-Regular.ttf", "the face that drew it")


def _letters_width(face: str, text: str) -> float:
    """How wide `text`'s letters are in a face we ship, spaces aside, at a size of 1."""
    program = pymupdf.Font(fontbuffer=face_bytes(FACES[face]))
    return sum(program.text_length(ch, fontsize=1) for ch in text if not ch.isspace())


def test_a_font_none_of_our_lists_name_is_logged_as_unlisted_never_by_its_name(
    app, browser, capsys, monkeypatch, tmp_path
):
    """A font's name can say whose file it is: only a name we list ourselves is logged.

    Its width list is Carlito's, so the face of its kind that draws is measured against it.
    """
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the worker's line is captured
    path = named_with_widths(
        str(tmp_path / "named.pdf"), "Carlito Regular", base_font=_UNLISTED
    )
    mine = browser()
    doc = upload(mine, Path(path).read_bytes()).json()
    [hello] = doc["spans"]
    edits = [_replaced(hello, "Hello here")]
    capsys.readouterr()

    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})

    assert_equal(exported.status_code, 200, "exported")
    output = capsys.readouterr().out
    [fields] = _substitute_lines(log_lines(output))
    named = (fields["font"], fields["face"], fields["same_widths"])
    expected = ("unlisted", "LiberationSans-Regular.ttf", "false")
    assert_equal(named, expected, "the font no list of ours names, and the face of its kind")
    own, drawn = (_letters_width(face, "Hello here") for face in ("Carlito Regular", _SANS))
    wider = (drawn - own) / own * 100
    assert_close(float(fields["width_delta_pct"]), wider, 0.01, "how much wider it sets them")
    assert_not_in(_UNLISTED, output, "the file's own name for its font")
    for text in (hello["text"], edits[0]["text"]):
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


def test_nothing_logged_carries_a_document_its_words_or_its_owner(
    app, browser, pdf_bytes, capsys, monkeypatch
):
    """Upload, edit, export: the log says what happened, never what was in it or whose."""
    monkeypatch.setattr(app.state, "pool", InProcess())  # so the workers' lines are captured
    mine = browser()
    capsys.readouterr()
    doc = upload(mine, pdf_bytes).json()
    delivery, made = span_starting(doc, 1, "Delivery"), span_starting(doc, 0, "Made")
    # The file's own font, and Times, which it only names: a similar font's line too.
    edits = [_replaced(span, span["text"].replace("14", "2")) for span in (delivery, made)]
    rendered = mine.post(
        f"/api/documents/{doc['id']}/render", json={"edits": edits, "scale": 2, "regions": []}
    )
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal((rendered.status_code, exported.status_code), (200, 200), "edited, exported")

    output = capsys.readouterr().out
    owner = mine.cookies["__Host-owner"]
    private = {
        "the document's id": doc["id"],
        "the owner token": owner,
        "the owner token's hash": hashlib.sha256(owner.encode()).hexdigest(),
        **{f"edit {position}'s text": edit["text"] for position, edit in enumerate(edits)},
        **{f"span {span['id']}'s text": span["text"] for span in doc["spans"]},
        **{f"font {font['name']}": font["name"] for font in doc["fonts"]},
        **{f"font {font['name']}'s shown name": font["shown_name"] for font in doc["fonts"]},
    }
    lines = log_lines(output)
    accessed = [line for line in lines if " request_done " in line]
    assert_equal(len(accessed), 3, "an access line per request")
    assert_equal(len(_substitute_lines(lines)), 1, "a line for the edit in a similar font")
    for what, value in private.items():
        assert_not_in(value, output, what)
