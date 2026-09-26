"""The command line, driven the way a person types it.

Each test calls `main()` with the words someone would type after `squidpdf`,
then checks the exit code, what was printed, and any file it wrote. The
pieces underneath are tested on their own in tests/core and tests/editing;
these only check that the commands wire them together.
"""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.cli import main
from squidpdf.core import MuPDFEngine, words
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true


def _span_id(pdf: str, needle: str) -> str:
    """The id of the first span whose text contains `needle`, as `squidpdf spans` lists it."""
    with MuPDFEngine(pdf) as eng:
        return next(s.id for s in eng.index() if needle in s.text)


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


def test_spans_with_a_page_lists_only_that_page(pdf, capsys):
    """Pages are counted from 0 on the command line, as the index counts them."""
    main(["spans", pdf, "--page", "1"])

    out = capsys.readouterr().out
    assert_in("Invoices are due", out, "the listing for page 1")
    assert_not_in("SERVICES AGREEMENT", out, "the listing for page 1")


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
    assert_in("--force", capsys.readouterr().out, "the refusal names the way past it")
    assert_true(not out_pdf.exists(), "no file is written when the edit is refused")


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


def test_redact_verifies_words_the_document_repeats_elsewhere(repeated, tmp_path, capsys):
    """A header on every page: removing one must not fail over the others."""
    out_pdf = tmp_path / "redacted.pdf"

    code = main(["redact", repeated, _span_id(repeated, "CONFIDENTIAL"), "-o", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf redact` on a line the document repeats")
    assert_in("checked gone by re-reading it", capsys.readouterr().out, "the redact output")
    assert_true(out_pdf.exists(), "the redacted file is kept")


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "{pdf}", "nope", "text"],
        ["edit", "{pdf}", "nope", "text"],
        ["redact", "{pdf}", "nope"],
    ],
    ids=["check", "edit", "redact"],
)
def test_an_unknown_span_id_fails_and_points_at_spans(pdf, argv, capsys):
    code = main([a.format(pdf=pdf) for a in argv])

    out = capsys.readouterr().out
    assert_equal(code, 1, f"exit code of `squidpdf {argv[0]}` with an unknown span id")
    assert_in(words.NO_SPAN, out, "the unknown-span error")
    assert_in("nope", out, "the unknown-span error names the id")
    assert_in("squidpdf spans", out, "the unknown-span error names the command to run")


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


def test_no_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])

    assert_equal(exc.value.code, 2, "argparse's exit code for a missing command")
    assert_in("usage: squidpdf", capsys.readouterr().err, "the usage message")
