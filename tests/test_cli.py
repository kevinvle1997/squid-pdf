"""Colour needs a real terminal, so its tests run the command in a process of its own."""

from __future__ import annotations

import errno
import os
import pty
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, get_args

import pymupdf
import pytest

from squidpdf.accounts.types import BillingEvent
from squidpdf.accounts.users import open_users
from squidpdf.cli import _MARKS, main  # noqa: PLC2701 (every fidelity has a mark)
from squidpdf.core import Fidelity, Message, open_pdf, words
from tests.conftest import OTHER_LINE, named_only, new_stream, with_hidden_copies
from tests.helpers import (
    assert_at_least,
    assert_equal,
    assert_false,
    assert_in,
    assert_not_in,
    assert_true,
)

_SECRET = b"a test's secret, long enough to sign with"


def _span_id(pdf: str, needle: str) -> str:
    with open_pdf(pdf) as engine:
        return next(s.id for s in engine.index() if needle in s.text)


def _read_until_closed(fd: int) -> str:
    chunks = []
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # once the writer closes, Linux ends the read with EIO, not b""
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(fd)
    return b"".join(chunks).decode()


def _run_in_a_terminal(
    args: list[str], *, terminal: Literal["stdout", "stderr"]
) -> dict[str, str]:
    reader, writer = pty.openpty()
    streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, terminal: writer}
    env = {name: value for name, value in os.environ.items() if name != "NO_COLOR"}
    ran = subprocess.run(
        [sys.executable, "-m", "squidpdf.cli", *args],
        stdout=streams["stdout"],
        stderr=streams["stderr"],
        env=env,
        check=False,
    )
    os.close(writer)
    shown = _read_until_closed(reader)
    if terminal == "stdout":
        return {"stdout": shown, "stderr": ran.stderr.decode()}
    return {"stdout": ran.stdout.decode(), "stderr": shown}


def _text(path) -> str:
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def test_spans_lists_every_span_and_a_summary(pdf, capsys):
    code = main(["spans", pdf])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf spans`")
    assert_in("SERVICES AGREEMENT", out, "the spans listing")
    assert_in("Invoices are due", out, "the spans listing")
    assert_in("keep the original font", out, "the summary line")


def test_every_fidelity_has_its_mark_in_the_spans_listing():
    assert_equal(sorted(_MARKS), sorted(get_args(Fidelity)), "the fidelities `spans` marks")


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


@pytest.mark.parametrize(("terminal", "colored"), [("stdout", False), ("stderr", True)])
def test_an_error_is_colored_only_when_stderr_is_a_terminal(tmp_path, terminal, colored):
    broken = tmp_path / "broken.pdf"
    broken.write_text("not a pdf")

    said = _run_in_a_terminal(["spans", str(broken)], terminal=terminal)

    assert_in(words.sentence("damaged"), said["stderr"], "what it says on stderr")
    assert_equal("\033[" in said["stderr"], colored, f"colour on stderr, {terminal} a terminal")


@pytest.mark.parametrize("command", ["spans", "report"])
def test_a_rate_below_the_warning_line_is_red_wherever_it_is_shown(tmp_path, command):
    named = named_only(str(tmp_path / "named.pdf"), "Calibri")

    said = _run_in_a_terminal([command, named], terminal="stdout")

    red = "\033[31m"
    assert_in(f"{red}0%", said["stdout"], f"the red rate `squidpdf {command}` gives")


def test_new_text_on_two_lines_is_a_usage_error(pdf, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", pdf, _span_id(pdf, "Invoices"), "Invoices\nare due"])

    assert_equal(exc.value.code, 2, "exit code of `squidpdf check` with a line break")
    assert_in("one line", capsys.readouterr().err, "the usage error")


def test_check_says_a_same_length_edit_fits(pdf, capsys):
    span_id = _span_id(pdf, "Invoices")

    code = main(["check", pdf, span_id, "Invoices are due within ninety days."])

    assert_equal(code, 0, "exit code of `squidpdf check`")
    out = capsys.readouterr().out
    assert_in("fits in place", out, "the check output")
    assert_not_in(words.sentence("signed_file"), out, "the check output, for a file not signed")


def test_check_explains_an_edit_that_will_not_fit(pdf, capsys):
    span_id = _span_id(pdf, "Invoices")
    longer = "Invoices are due within thirty days of receipt, without any deduction."

    code = main(["check", pdf, span_id, longer])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf check`: it only reports, never fails")
    assert_not_in("fits in place", out, "the check output for a far longer edit")
    _percent, too_long = words.sentence("too_long").split("{percent}%")
    assert_in(too_long, out, "how much too long it is, for its place")
    assert_in("as-is", out, "the way out it offers")


def test_edit_replaces_the_text_and_saves(pdf, tmp_path, capsys):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "edited.pdf"

    shorter = "Invoices are due within ninety days."

    code = main(["edit", pdf, span_id, shorter, "-o", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf edit`")
    out = capsys.readouterr().out
    assert_in("saved", out, "the edit output")
    assert_not_in(words.sentence("signed_file"), out, "the edit output, for a file not signed")
    saved = _text(out_pdf)
    assert_in("ninety days", saved, "the saved PDF after an edit")
    assert_not_in("thirty days", saved, "the saved PDF after an edit")
    with pymupdf.open(out_pdf) as doc:
        producer = doc.xref_get_key(-1, "Info/Producer")  # -1: the file's trailer
    assert_equal(producer, ("string", "squidpdf"), "the program the Info names")


@pytest.mark.parametrize(
    ("command", "said"),
    [
        (["check", "Other lime"], "signed_file"),
        (["edit", "Other lime", "-o", "out.pdf"], "signed_file"),
        (["redact", "-o", "out.pdf"], "signatures_removed"),
    ],
    ids=["check", "edit", "redact"],
)
def test_a_signed_file_says_what_the_command_does_to_its_signature(
    tmp_path, monkeypatch, capsys, command, said
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "signed.pdf").write_bytes(with_hidden_copies(["signature"]))
    span_id = _span_id("signed.pdf", OTHER_LINE)
    name, *rest = command

    code = main([name, "signed.pdf", span_id, *rest])

    assert_equal(code, 0, f"exit code of `squidpdf {name}`")
    out = capsys.readouterr().out
    for key in ("signed_file", "signatures_removed"):
        assert_equal(words.sentence(key) in out, key == said, f"`squidpdf {name}` saying {key}")


def test_edit_refuses_what_will_not_fit_and_writes_nothing(pdf, tmp_path, capsys):
    span_id = _span_id(pdf, "Invoices")
    out_pdf = tmp_path / "edited.pdf"
    longer = "Invoices are due within thirty days of receipt, without any deduction."

    code = main(["edit", pdf, span_id, longer, "-o", str(out_pdf)])

    assert_equal(code, 1, "exit code of `squidpdf edit` for an edit that will not fit")
    assert_in("--force", capsys.readouterr().err, "the refusal names the way past it")
    assert_false(out_pdf.exists(), "no file is written when the edit is refused")


def test_edit_takes_a_way_out_check_offered_without_force(pdf, tmp_path, capsys):
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
    out = capsys.readouterr().out
    assert_in("checked gone by re-reading it", out, "the redact output")
    assert_not_in(words.sentence("signatures_removed"), out, "the redact output, unsigned")
    assert_not_in("Invoices are due", _text(out_pdf), "the saved PDF after a redact")


_PLAIN_XMP = str(Path(__file__).parents[1] / "fixtures" / "plain_xmp_aes128.pdf")


@pytest.mark.parametrize(
    "command",
    [["edit", "Yearly report"], ["redact"]],
    ids=["edit", "redact"],
)
def test_a_protected_files_plain_xmp_is_left_out_of_the_saved_file_and_said(
    tmp_path, capsys, command
):
    out_pdf = tmp_path / "out.pdf"
    name, *rest = command

    code = main(
        [name, _PLAIN_XMP, _span_id(_PLAIN_XMP, "Quarterly"), *rest, "-o", str(out_pdf)]
    )

    assert_equal(code, 0, f"exit code of `squidpdf {name}`")
    with pymupdf.open(out_pdf) as saved:
        xmp = [
            xref
            for xref in range(1, saved.xref_length())
            if saved.xref_get_key(xref, "Type") == ("name", "/Metadata")
        ]
    assert_equal(xmp, [], "the XMP left in the saved file")
    assert_in(words.sentence("plain_xmp_dropped"), capsys.readouterr().out, "what it says")


_KEPT_LINE = "Secret plan"
_TILING = {
    "PatternType": "1",
    "PaintType": "1",
    "TilingType": "1",
    "BBox": "[0 0 20 20]",
    "XStep": "20",
    "YStep": "20",
    "Resources": "<<>>",
}


def _kept_where_no_redaction_reaches(path: Path) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 600), _KEPT_LINE, fontname="helv", fontsize=12)
    tile = f"/Span <</ActualText ({_KEPT_LINE}) /Bad>> BDC 10 10 20 20 re f EMC"
    pattern = new_stream(doc, tile, **_TILING)
    _kind, resources = doc.xref_get_key(page.xref, "Resources")
    doc.xref_set_key(int(resources.split()[0]), "Pattern/P1", f"{pattern} 0 R")
    [contents] = page.get_contents()
    painted = doc.xref_stream(contents) + b"\n/Pattern cs /P1 scn 10 10 20 20 re f"
    doc.update_stream(contents, painted)
    doc.save(path)
    return str(path)


def test_redact_the_re_read_cannot_confirm_keeps_no_file_and_says_why(tmp_path, capsys):
    pdf = _kept_where_no_redaction_reaches(tmp_path / "kept.pdf")
    out_pdf = tmp_path / "redacted.pdf"

    code = main(["redact", pdf, _span_id(pdf, _KEPT_LINE), "-o", str(out_pdf)])

    said = words.sentence("redaction_failed").format(text=_KEPT_LINE, page=1)
    assert_equal(code, 1, "exit code of `squidpdf redact` when the text is still there")
    assert_in(said, capsys.readouterr().err, "the redact output")
    assert_false(out_pdf.exists(), "a file kept with the text still in it")


def _copy_onto_a_full_disk(*_paths: object) -> None:
    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


def test_a_save_that_cannot_be_written_names_its_path_and_leaves_the_file_as_it_was(
    pdf, tmp_path, monkeypatch, capsys
):
    out_pdf = tmp_path / "edited.pdf"
    out_pdf.write_bytes(b"what was there")
    monkeypatch.setattr(shutil, "copyfile", _copy_onto_a_full_disk)

    code = main(["edit", pdf, _span_id(pdf, "thirty days"), "ninety days", "-o", str(out_pdf)])

    said = words.render(
        Message("not_saved", {"path": str(out_pdf), "why": os.strerror(errno.ENOSPC)})
    )
    assert_equal(code, 1, "exit code of `squidpdf edit` when its file can't be written")
    assert_in(said, capsys.readouterr().err, "the edit output")
    assert_equal(out_pdf.read_bytes(), b"what was there", "the file at the path after")
    assert_equal(list(tmp_path.iterdir()), [out_pdf], "the files in its folder after")


def test_an_unknown_span_id_fails_and_points_at_spans(pdf, capsys):
    code = main(["edit", pdf, "nope", "text"])

    err = capsys.readouterr().err
    assert_equal(code, 1, "exit code of `squidpdf edit` with an unknown span id")
    assert_in(words.sentence("no_span"), err, "the unknown-span error")
    assert_in("nope", err, "the unknown-span error names the id")
    assert_in("squidpdf spans", err, "the unknown-span error names the command to run")


def test_report_rates_each_file_and_carries_on_past_a_bad_one(pdf, tmp_path, capsys):
    broken = tmp_path / "broken.pdf"
    broken.write_text("not a pdf")

    code = main(["report", pdf, str(broken)])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf report` with one bad file")
    assert_in("failed", out, "the report row for the bad file")
    assert_in("broken.pdf", out, "the report row for the bad file")
    assert_in("spans keep the original font", out, "the report's overall line")


def test_report_fails_when_it_could_read_no_file(tmp_path, capsys):
    broken = tmp_path / "broken.pdf"
    broken.write_text("not a pdf")

    code = main(["report", str(broken)])

    assert_equal(code, 1, "exit code of `squidpdf report` with no file it could read")
    assert_in("failed", capsys.readouterr().out, "the report row for the bad file")


@pytest.mark.parametrize(("command", "exit_code"), [("spans", 0), ("report", 1)])
def test_a_document_with_no_text_has_no_green_rate(tmp_path, capsys, command, exit_code):
    blank = tmp_path / "blank.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(blank)

    code = main([command, str(blank)])

    out = capsys.readouterr().out
    assert_equal(code, exit_code, f"exit code of `squidpdf {command}` on a page with no text")
    assert_in("no text", out, f"what `squidpdf {command}` says of it")
    assert_not_in("%", out, f"a green rate in `squidpdf {command}`")
    assert_not_in("failed", out, f"what `squidpdf {command}` says of a file it read")


def test_fixture_writes_a_pdf_the_other_commands_can_read(tmp_path, capsys):
    out_pdf = tmp_path / "sample.pdf"

    code = main(["fixture", str(out_pdf)])

    assert_equal(code, 0, "exit code of `squidpdf fixture`")
    assert_in("SERVICES AGREEMENT", _text(out_pdf), "the fixture PDF")
    capsys.readouterr()
    assert_equal(main(["spans", str(out_pdf)]), 0, "exit code of `squidpdf spans` on it")


def test_fixture_with_pages_writes_a_long_contract_of_full_pages(tmp_path):
    out_pdf = tmp_path / "dense.pdf"

    code = main(["fixture", str(out_pdf), "--pages", "3"])

    assert_equal(code, 0, "exit code of `squidpdf fixture --pages`")
    with open_pdf(str(out_pdf)) as engine:
        spans = engine.index()
    assert_equal({span.page for span in spans}, {0, 1, 2}, "the pages the spans are on")
    assert_at_least(len(spans.by_id), 3 * 40 + 1, "spans on three of a contract's full pages")
    assert_in("This agreement is made on 14 March 2026 between", _text(out_pdf), "page 1")


def test_fixture_with_pages_never_replaces_the_committed_sample(tmp_path, monkeypatch, capsys):
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


_TERMS = Path(__file__).parents[1] / "fixtures" / "terms.md"


@pytest.mark.parametrize("named", ["as its argument", "by SQUIDPDF_TERMS"])
def test_terms_checks_a_terms_file_as_the_server_reads_it(monkeypatch, capsys, named):
    if named == "as its argument":
        code = main(["terms", str(_TERMS)])
    else:
        monkeypatch.setenv("SQUIDPDF_TERMS", str(_TERMS))
        code = main(["terms"])

    out = capsys.readouterr().out
    assert_equal(code, 0, "exit code of `squidpdf terms` on terms the page can show")
    for shown in (
        "Example terms",
        "Last changed 1 January 2000",
        "First lead.",
        "Second lead.",
    ):
        assert_in(shown, out, "what the page will show")


def test_terms_says_why_the_server_would_refuse_a_file(tmp_path, capsys):
    path = tmp_path / "terms.md"
    path.write_text("# T\n\nNo lead.\n", encoding="utf-8")

    code = main(["terms", str(path)])

    assert_equal(code, 1, "exit code of `squidpdf terms` on terms the page can't show")
    refusal = words.render(Message("terms_no_lead", {"path": str(path), "line": 3}))
    assert_in(refusal, capsys.readouterr().err, "why")


def test_terms_with_no_file_named_says_so(monkeypatch, capsys):
    monkeypatch.delenv("SQUIDPDF_TERMS", raising=False)

    code = main(["terms"])

    assert_equal(code, 1, "exit code of `squidpdf terms` with no file")
    assert_in(words.render(Message("terms_none_named")), capsys.readouterr().err, "why")


@pytest.fixture
def database(tmp_path, monkeypatch) -> Path:
    database = tmp_path / "users.sqlite3"
    monkeypatch.setenv("SQUIDPDF_DATABASE", str(database))
    monkeypatch.setenv("SQUIDPDF_SECRET", _SECRET.decode())
    return database


def test_invite_lets_someone_in_and_unlimited_lifts_their_limit_until_invited_again(
    database, capsys
):
    assert_equal(main(["invite", " Friend@Example.com", "--unlimited"]), 0, "exit code")
    assert_in("friend@example.com is invited, and may use", capsys.readouterr().out, "said")
    friend = open_users(database).find("friend@example.com")
    assert_equal(friend and friend.is_unlimited, True, "no document limit")
    assert_equal(main(["invite", "friend@example.com"]), 0, "invited again, without it")
    again = open_users(database).find("friend@example.com")
    assert_equal(again and again.is_unlimited, False, "the limit is back")


def test_remove_keeps_an_account_whose_month_is_paid_until_its_subscription_ends(
    database, capsys
):
    users = open_users(database)
    account = users.invite("payer@example.com", now=0, is_unlimited=False, secret=_SECRET).id
    now = int(time.time())

    def paid_until(until: int | None, *, event: str) -> None:
        billing = BillingEvent(
            event,
            type="customer.subscription.updated",
            created=now,
            customer="cus_test",
            account=account,
            outcome="applied",
            paid_until=until,
            paid_read_at=now * 1000 + (0 if until is not None else 1),
        )
        users.apply_billing(billing, now=now)

    paid_until(now + 3600, event="evt_paid")
    assert_equal(main(["remove", "payer@example.com"]), 1, "exit code, still paying")
    assert_in("Cancel their subscription in Stripe", capsys.readouterr().err, "said")
    assert_equal(users.find("payer@example.com") is None, False, "the account is kept")
    paid_until(None, event="evt_cancelled")
    assert_equal(main(["remove", "payer@example.com"]), 0, "exit code, cancelled")


def test_billing_says_whether_an_account_is_paid_and_lists_what_stripe_said(database, capsys):
    users = open_users(database)
    account = users.invite("payer@example.com", now=0, is_unlimited=False, secret=_SECRET).id
    users.keep_customer(account, "cus_test")
    assert_equal(main(["billing", "payer@example.com"]), 0, "exit code, never paid")
    assert_in("payer@example.com has no paid month", capsys.readouterr().out, "said")
    paid = BillingEvent(
        "evt_paid",
        type="invoice.paid",
        created=0,
        customer="cus_test",
        account=account,
        outcome="applied",
        paid_until=2**31,
        paid_read_at=1,
    )
    users.apply_billing(paid, now=0)
    assert_equal(main(["billing", "payer@example.com"]), 0, "exit code, paid")
    shown = capsys.readouterr().out
    assert_in("paid until 2038-01-19 03:14 UTC", shown, "until when")
    assert_in("cus_test", shown, "the Stripe customer")
    assert_in("invoice.paid", shown, "the event")
    assert_equal(main(["billing", "stranger@example.com"]), 1, "exit code, no account")


def test_billing_set_up_in_stripe_but_not_here_is_left_as_it_is(database, capsys):
    users = open_users(database)
    account = users.invite("payer@example.com", now=0, is_unlimited=False, secret=_SECRET).id
    users.keep_customer(account, "cus_test")
    assert_equal(main(["remove", "payer@example.com"]), 1, "exit code, remove")
    assert_in("isn't set up to ask Stripe", capsys.readouterr().err, "said")
    assert_equal(users.find("payer@example.com") is None, False, "the account is kept")
    assert_equal(main(["billing", "payer@example.com", "--resync"]), 1, "exit code, sync")
    assert_in("isn't set up to ask Stripe", capsys.readouterr().err, "said")


def test_invite_clears_a_bounce_kept_for_the_address_before_it_had_an_account(database):
    users = open_users(database)
    users.note_bounce("friend@example.com", secret=_SECRET, now=0)
    assert_equal(users.has_bounced("friend@example.com", secret=_SECRET), True, "bounced")
    assert_equal(main(["invite", "friend@example.com"]), 0, "exit code")
    assert_equal(users.has_bounced("friend@example.com", secret=_SECRET), False, "cleared")
    users.remove("friend@example.com", now=1, secret=_SECRET)
    assert_equal(users.has_bounced("friend@example.com", secret=_SECRET), False, "taken off")


def test_invite_without_the_servers_secret_invites_no_one_and_says_so(
    tmp_path, monkeypatch, capsys
):
    database = tmp_path / "users.sqlite3"
    monkeypatch.setenv("SQUIDPDF_DATABASE", str(database))
    monkeypatch.delenv("SQUIDPDF_SECRET", raising=False)
    assert_equal(main(["invite", "friend@example.com"]), 1, "exit code")
    assert_in("SQUIDPDF_SECRET", capsys.readouterr().err, "what it needs")
    assert_equal(open_users(database).find("friend@example.com"), None, "no one invited")


def test_invite_on_a_server_with_no_database_says_so(monkeypatch, capsys):
    monkeypatch.delenv("SQUIDPDF_DATABASE", raising=False)
    assert_equal(main(["invite", "friend@example.com"]), 1, "exit code")
    assert_in("SQUIDPDF_DATABASE names no database", capsys.readouterr().err, "said")


def test_remove_takes_an_account_off_and_says_when_there_was_none(database, capsys):
    main(["invite", "friend@example.com", "--unlimited"])
    capsys.readouterr()
    assert_equal(main(["remove", " Friend@Example.com"]), 0, "exit code")
    assert_in("friend@example.com is removed", capsys.readouterr().out, "said")
    assert_equal(open_users(database).find("friend@example.com"), None, "no account kept")
    assert_equal(main(["remove", "friend@example.com"]), 1, "exit code, removed again")
    assert_in("has no account here", capsys.readouterr().err, "said")
