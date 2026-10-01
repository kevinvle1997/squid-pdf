"""The command line, driven the way a person types it.

Each test calls `main()` with the words someone would type after `squidpdf`,
then checks the exit code, what was printed, and any file it wrote. The
pieces underneath are tested on their own in tests/core and tests/editing;
these only check that the commands wire them together.
"""

from __future__ import annotations

import shutil

import pymupdf
import pytest

from squidpdf.cli import main
from squidpdf.core import Engine, open_pdf, words
from tests.helpers import assert_equal, assert_false, assert_in, assert_not_in, assert_true


def _span_id(pdf: str, needle: str) -> str:
    """The id of the first span whose text contains `needle`, as `squidpdf spans` lists it."""
    with open_pdf(pdf) as engine:
        return next(s.id for s in engine.index() if needle in s.text)


def _text(path) -> str:
    """All the text MuPDF reads back from a saved PDF."""
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def test_spans_lists_every_span_and_a_summary(pdf, capsys):
    code = main(["spans", pdf])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf spans`")
    assert_in("SERVICES AGREEMENT", out, "the spans listing")
    assert_in("Invoices are due", out, "the spans listing")
    assert_in("keep the original font", out, "the summary line")


def test_spans_on_one_page_counts_that_page_from_1_as_it_prints_it(pdf, capsys):
    code = main(["spans", pdf, "-p", "2"])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf spans -p 2`")
    assert_in("Invoices are due", out, "a span on the second page")
    assert_not_in("SERVICES AGREEMENT", out, "a span on the first page")
    assert_in("\n  2 spans", out, "the summary, of the page shown")


def test_a_pdf_that_wont_open_says_why_without_a_traceback(tmp_path, capsys):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8)

    code = main(["spans", str(broken)])

    said = capsys.readouterr()
    assert_equal(code, 1, "exit code of `squidpdf spans` on a damaged PDF")
    assert_in(words.sentence("damaged"), said.err, "what it says")
    assert_not_in("Traceback", said.err, "what it says")


def test_new_text_on_two_lines_is_a_usage_error(pdf, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", pdf, _span_id(pdf, "Invoices"), "Invoices\nare due"])

    assert_equal(exc.value.code, 2, "exit code of `squidpdf check` with a line break")
    assert_in("one line", capsys.readouterr().err, "the usage error")


def test_check_says_a_same_length_edit_fits(pdf, capsys):
    span_id = _span_id(pdf, "Invoices")

    code = main(["check", pdf, span_id, "Invoices are due within ninety days."])

    assert_equal(code, 0, "exit code of `squidpdf check`")
    assert_in("fits in place", capsys.readouterr().out, "the check output")


def test_check_explains_an_edit_that_will_not_fit(pdf, capsys):
    span_id = _span_id(pdf, "Invoices")
    longer = "Invoices are due within thirty days of receipt, without any deduction."

    code = main(["check", pdf, span_id, longer])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf check`: it only reports, never fails")
    assert_not_in("fits in place", out, "the check output for a far longer edit")


def test_edit_replaces_the_text_and_saves(pdf, tmp_path, capsys):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "edited.pdf"

    shorter = "Invoices are due within ninety days."

    code = main(["edit", pdf, span_id, shorter, "-o", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf edit`")
    assert_in("saved", capsys.readouterr().out, "the edit output")
    saved = _text(out_pdf)
    assert_in("ninety days", saved, "the saved PDF after an edit")
    assert_not_in("thirty days", saved, "the saved PDF after an edit")


def test_edit_refuses_what_will_not_fit_and_writes_nothing(pdf, tmp_path, capsys):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "edited.pdf"
    longer = "Invoices are due within thirty days of receipt, without any deduction."

    code = main(["edit", pdf, span_id, longer, "-o", str(out_pdf)])

    assert_equal(code, 1, "exit code of `squidpdf edit` for an edit that will not fit")
    assert_in("--force", capsys.readouterr().err, "the refusal names the way past it")
    assert_true(not out_pdf.exists(), "no file is written when the edit is refused")


def test_edit_takes_a_way_out_check_offered_without_force(pdf, tmp_path, capsys):
    """`check` offers shrink for a line a little too long; `edit` can take it."""
    span_id = _span_id(pdf, "Made on")
    out_pdf = tmp_path / "shrunk.pdf"
    longer = "Made on 14 March 2026 between Wescott and Rowe!!"

    code = main(["edit", pdf, span_id, longer, "-o", str(out_pdf), "--strategy", "shrink"])

    assert_equal(code, 0, "exit code of `squidpdf edit --strategy shrink`")
    assert_in("saved", capsys.readouterr().out, "the edit output")
    assert_in("Rowe!!", _text(out_pdf), "the saved PDF after a shrunk edit")


def test_edit_with_force_saves_what_will_not_fit(pdf, tmp_path):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "edited.pdf"
    longer = "Invoices are due within thirty days of receipt, without any deduction."

    code = main(["edit", pdf, span_id, longer, "-o", str(out_pdf), "--force"])

    assert_equal(code, 0, "exit code of `squidpdf edit --force`")
    assert_in("without any deduction", _text(out_pdf), "the saved PDF after a forced edit")


def test_redact_removes_the_text_and_says_it_verified(pdf, tmp_path, capsys):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "redacted.pdf"

    code = main(["redact", pdf, span_id, "-o", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf redact`")
    assert_in("checked gone by re-reading it", capsys.readouterr().out, "the redact output")
    assert_not_in("Invoices are due", _text(out_pdf), "the saved PDF after a redact")


def test_redact_the_re_read_cannot_confirm_keeps_no_file_and_says_why(
    pdf, tmp_path, monkeypatch, capsys
):
    """Text still in the saved file means no file is kept."""
    monkeypatch.setattr(Engine, "still_there", lambda _engine, spans: list(spans))
    out_pdf = tmp_path / "redacted.pdf"

    code = main(["redact", pdf, _span_id(pdf, "Invoices"), "-o", str(out_pdf)])

    said = words.sentence("redaction_failed").format(
        text="Invoices are due within thirty days.", page=2
    )
    assert_equal(code, 1, "exit code of `squidpdf redact` when the text is still there")
    assert_in(said, capsys.readouterr().err, "the redact output")
    assert_false(out_pdf.exists(), "a file kept with the text still in it")


def test_an_unknown_span_id_fails_and_points_at_spans(pdf, capsys):
    code = main(["edit", pdf, "nope", "text"])

    err = capsys.readouterr().err
    assert_equal(code, 1, "exit code of `squidpdf edit` with an unknown span id")
    assert_in(words.sentence("no_span"), err, "the unknown-span error")
    assert_in("nope", err, "the unknown-span error names the id")
    assert_in("squidpdf spans", err, "the unknown-span error names the command to run")


def test_report_rates_each_file_and_carries_on_past_a_bad_one(pdf, tmp_path, capsys):
    """One unreadable file must not stop the rest of the corpus being scored."""
    broken = tmp_path / "broken.pdf"
    broken.write_text("not a pdf")

    code = main(["report", pdf, str(broken)])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf report` with one bad file")
    assert_in("failed", out, "the report row for the bad file")
    assert_in("broken.pdf", out, "the report row for the bad file")
    assert_in("spans keep the original font", out, "the report's overall line")


def test_fixture_writes_a_pdf_the_other_commands_can_read(tmp_path, capsys):
    out_pdf = tmp_path / "sample.pdf"

    code = main(["fixture", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf fixture`")
    assert_in("SERVICES AGREEMENT", _text(out_pdf), "the fixture PDF")
    capsys.readouterr()
    assert_equal(main(["spans", str(out_pdf)]), 0, "exit code of `squidpdf spans` on it")


def test_fixture_with_pages_writes_a_long_contract_of_full_pages(tmp_path):
    """The browser's performance probe opens it: every page as full as a contract's."""
    out_pdf = tmp_path / "dense.pdf"

    code = main(["fixture", str(out_pdf), "--pages", "3"])

    assert_equal(code, 0, "exit code of `squidpdf fixture --pages`")
    with open_pdf(str(out_pdf)) as engine:
        spans = engine.index()
    assert_equal({span.page for span in spans}, {0, 1, 2}, "the pages the spans are on")
    assert_true(len(spans) > 3 * 40, f"a contract's page is full, got {len(spans)} spans")
    assert_in("This agreement is made on 14 March 2026 between", _text(out_pdf), "page 1")


def test_fixture_with_pages_never_replaces_the_committed_sample(tmp_path, monkeypatch, capsys):
    """The tests read fixtures/sample.pdf, so a long contract must be saved elsewhere."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fixtures").mkdir()

    with pytest.raises(SystemExit) as exc:
        main(["fixture", "--pages", "2"])

    assert_equal(exc.value.code, 2, "exit code of `squidpdf fixture --pages` with no file")
    assert_in("--pages would replace", capsys.readouterr().err, "the usage error")
    assert_false((tmp_path / "fixtures" / "sample.pdf").exists(), "the sample, untouched")


def test_a_pdf_that_is_not_there_is_a_usage_error_not_a_crash(tmp_path, capsys):
    missing = str(tmp_path / "missing.pdf")

    with pytest.raises(SystemExit) as exc:
        main(["spans", missing])

    assert_equal(exc.value.code, 2, "exit code of `squidpdf spans` for a missing file")
    assert_in(f"there's no file at {missing}", capsys.readouterr().err, "the usage error")


def test_saving_over_the_pdf_being_read_is_refused(pdf, tmp_path, capsys):
    """Were anything to go wrong, the original would be lost with it."""
    copy = tmp_path / "copy.pdf"
    shutil.copy(pdf, copy)
    before = copy.read_bytes()
    span_id = _span_id(pdf, "Invoices")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "edit",
                str(copy),
                span_id,
                "Invoices are due within ninety days.",
                "-o",
                str(copy),
            ]
        )

    assert_equal(exc.value.code, 2, "exit code of `squidpdf edit` writing over its input")
    assert_in("is the PDF being read", capsys.readouterr().err, "the usage error")
    assert_true(copy.read_bytes() == before, "the PDF being read is unchanged")
