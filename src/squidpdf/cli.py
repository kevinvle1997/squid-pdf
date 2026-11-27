"""Command line for the engine.

squidpdf spans   file.pdf              what is editable, and how it would edit
squidpdf check   file.pdf ID "text"    what would happen if you typed that
squidpdf edit    file.pdf ID "text" -o out.pdf [--strategy shrink]
squidpdf redact  file.pdf ID -o out.pdf
squidpdf report  file.pdf [...]        the one number that matters
squidpdf fixture out.pdf               a sample document to try it on
squidpdf fixture out.pdf --pages N     a long contract, for timing the browser
squidpdf terms   [terms.md]            check a terms file as the server will read it
squidpdf invite  email [--unlimited]   let someone in; --unlimited lifts their document limit
squidpdf remove  email                 take someone's account off, and sign them out everywhere
squidpdf billing email [--resync]      what Stripe said of an account; --resync asks again
"""

from __future__ import annotations

import argparse
import os
import posixpath
import shutil
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TextIO, assert_never, get_args

from squidpdf.accounts.billing.removal import remove_account
from squidpdf.accounts.billing.webhook import resync
from squidpdf.accounts.deployment import (
    AccountsUnset,
    Billing,
    deployed_accounts,
    named_secret,
    named_users,
)
from squidpdf.accounts.errors import PaymentsUnavailable
from squidpdf.accounts.types import BillingEvent
from squidpdf.accounts.users import User, UserStore, checked_email
from squidpdf.core import (
    DEFAULT_DRIVER_FACTORY,
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Engine,
    Fidelity,
    FidelityReport,
    FontSources,
    FreeSpace,
    Message,
    Problem,
    Span,
    SpanIndex,
    google_fonts,
    green_rate,
    open_pdf,
    result_of,
    whole_run,
    words,
    write_dense,
    write_sample,
)
from squidpdf.editing import (
    Redact,
    RedactionFailed,
    RedactionMoved,
    RedactionUnchecked,
    Replace,
    Strategy,
    check_text,
    replace_fit,
    save_edited,
)
from squidpdf.terms.terms_file import TermsUnreadable, named_terms_file, terms_in

_COLORS = ("\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def _colors_of(stream: TextIO) -> tuple[str, ...]:
    colored = stream.isatty() and "NO_COLOR" not in os.environ
    return _COLORS if colored else ("",) * len(_COLORS)


_DIM, _RED, _GREEN, _YELLOW, _OFF = _colors_of(sys.stdout)

_MARKS: dict[Fidelity, str] = {
    "exact": f"{_GREEN}exact{_OFF}      ",
    "approximate": f"{_YELLOW}approximate{_OFF}",
    "substitute": f"{_YELLOW}substitute{_OFF} ",
}

_TEXT_PREVIEW_LEN = 43
_NAME_COL_WIDTH = 38
_NAME_COL_PAD = 40
# The committed sample: the browser's end-to-end test and deploy/check.sh read it.
_SAMPLE = "fixtures/sample.pdf"


def _opened(pdf: str) -> Engine:
    return open_pdf(pdf, sources=FontSources(google=google_fonts()))


def _spans_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("-p", "--page", type=int, default=None, help="only this page, from 1")


def _cmd_spans(args: argparse.Namespace) -> int:
    with _opened(args.pdf) as engine:
        index = engine.index()
        reports = {report.span_id: report for report in engine.assess(index)}

        shown = [s for s in index if args.page is None or s.page == args.page - 1]
        for span in shown:
            print(_listing_of(span, reports[span.id]))
        _summary([reports[span.id] for span in shown])
    return 0


def _listing_of(span: Span, report: FidelityReport) -> str:
    note = f" -> {report.substitute}" if report.substitute else ""
    fragments = f" {_DIM}({len(span.fragments)} fragments){_OFF}" if span.merged else ""
    fits = len(span.text) <= _TEXT_PREVIEW_LEN + len("...")
    preview = span.text if fits else span.text[:_TEXT_PREVIEW_LEN] + "..."
    lines = [
        f"  {span.id}  p{span.page + 1}  {_MARKS[report.state]}  "
        f"{_DIM}{span.font}{note} {span.size}pt{_OFF}{fragments}",
        f"           {preview}",
    ]
    if report.state == "approximate" and report.why is not None:
        lines.append(f"           {_DIM}{words.render(report.why)}{_OFF}")
    return "\n".join(lines)


def _summary(reports: list[FidelityReport]) -> None:
    rate = green_rate(reports)
    counts = Counter(report.state for report in reports)
    summary_line = (
        f"\n  {len(reports)} spans · {counts['exact']} exact"
        f" · {counts['approximate']} approximate"
        f" · {counts['substitute']} substitute"
    )
    if rate is None:
        print(f"{summary_line} · no text")
        return
    print(f"{summary_line} · {_rate_color(rate)}{rate:.0%} keep the original font{_OFF}")


class _UnknownSpan(Exception):
    def __init__(self, span_id: str) -> None:
        super().__init__(span_id)
        self.span_id = span_id


class _NotSaved(Exception):
    def __init__(self, path: str, why: str) -> None:
        super().__init__(path)
        self.path = path
        self.why = why


@dataclass(frozen=True, slots=True, eq=False)
class _Opened:
    engine: Engine
    index: SpanIndex
    span: Span
    rooms: dict[str, float]


@contextmanager
def _opened_at(pdf: str, span_id: str) -> Iterator[_Opened]:
    with _opened(pdf) as engine:
        index = engine.index()
        span = index.by_id.get(span_id)
        if span is None:
            raise _UnknownSpan(span_id)
        yield _Opened(engine, index, span, rooms=engine.rooms(index, [span]))


def _signature_notices(engine: Engine) -> list[Message]:
    return [Message("signed_file")] if engine.holds_signature() else []


def _plain_xmp_notices(dropped: bool) -> list[Message]:
    return [Message("plain_xmp_dropped")] if dropped else []


@contextmanager
def _saved_apart(out: str) -> Iterator[str]:
    """So a failed save or check leaves `out` as it was."""
    with tempfile.TemporaryDirectory() as scratch:
        saved = Path(scratch) / "saved.pdf"
        yield str(saved)
        _put_at(saved, out)


def _put_at(saved: Path, out: str) -> None:
    try:
        with tempfile.TemporaryDirectory(
            dir=Path(out).parent, ignore_cleanup_errors=True
        ) as beside:
            staged = Path(beside) / "saved.pdf"
            shutil.copyfile(saved, staged)
            staged.replace(out)
    except OSError as failed:
        raise _NotSaved(out, failed.strerror or str(failed)) from None


def _check_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=_one_line)


def _cmd_check(args: argparse.Namespace) -> int:
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        fit = replace_fit(opened.engine, span, args.text, room_pt=opened.rooms[span.id]).report
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {_DIM}{span.font} {span.size}pt{_OFF}")
        print(f"  width {fit.delta_pt:+.2f} pt")
        for said in _signature_notices(opened.engine):
            print(f"  {_YELLOW}{words.render(said)}{_OFF}")

        described = words.render_all(fit.describe())
        if not described:
            print(f"  {_GREEN}fits in place{_OFF}\n")
            return 0
        print(f"  {_RED}{described}{_OFF}")
        for option in fit.options:
            label, detail = words.render(option.label), words.render(option.detail)
            print(f"    {_DIM}{option.name:<9}{_OFF} {label}{_DIM}: {detail}{_OFF}")
        print()
        return 0


def _edit_arguments(command: argparse.ArgumentParser) -> None:
    _check_arguments(command)
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)
    command.add_argument("--force", action="store_true", help="edit even if it will not fit")
    command.add_argument(
        "--strategy",
        choices=get_args(Strategy.__value__),
        default="as-is",
        help="a way out `check` offers: shrink or condense, to fit",
    )


def _overwrite_refusal(args: argparse.Namespace) -> str | None:
    # Saving over the PDF being read would lose the original if anything went wrong.
    if _same_file(args.pdf, args.out):
        return f"-o {args.out} is the PDF being read; save to a new file"
    return None


def _same_file(pdf: str, out: str) -> bool:
    return Path(out).exists() and Path(out).samefile(pdf)


def _cmd_edit(args: argparse.Namespace) -> int:
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        replace = Replace(span.id, args.text, strategy=args.strategy)
        fit = replace_fit(
            opened.engine,
            span,
            replace.text,
            room_pt=opened.rooms[span.id],
            strategy=replace.strategy,
        ).report
        # A way out that was offered makes a long line fit; missing letters still don't.
        fitted = not fit.missing and fit.strategy != "as-is"
        refused = not (fit.ok or fitted or args.force)
        if refused:
            # `render_all` gives None for no messages, but a fit that isn't ok always has one.
            described = str(words.render_all(fit.describe()))
            return _fail(described, hint="(pass --force to do it anyway)")

        # As the upload does: MuPDF would write that XMP garbled.
        plain_xmp_dropped = opened.engine.drop_plain_xmp()
        file_notices = _plain_xmp_notices(plain_xmp_dropped) + _signature_notices(opened.engine)
        with _saved_apart(args.out) as to:
            saved = save_edited(
                opened.engine,
                opened.index,
                free_space=FreeSpace(opened.rooms, lines=[]),
                edits=[replace],
                to=to,
                driver_factory=DEFAULT_DRIVER_FACTORY,
            )
    print(f"\n  {span.text!r} -> {args.text!r}")
    for said in (
        [notice.detail for notice in saved.applied.notices] + saved.notices + file_notices
    ):
        print(f"  {_YELLOW}{words.render(said)}{_OFF}")
    print(f"  {_GREEN}saved{_OFF} {args.out}\n")
    return 0


def _redact_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)


def _cmd_redact(args: argparse.Namespace) -> int:
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        plain_xmp_dropped = opened.engine.drop_plain_xmp()
        hidden = opened.engine.redaction.hidden_places([whole_run(span)])[span.id]
        removal_notices = [Message("signatures_removed")] if "signatures" in hidden else []
        try:
            with _saved_apart(args.out) as to:
                saved = save_edited(
                    opened.engine,
                    opened.index,
                    free_space=FreeSpace(opened.rooms, lines=[]),
                    edits=[Redact(span.id)],
                    to=to,
                    driver_factory=DEFAULT_DRIVER_FACTORY,
                )
        except (RedactionFailed, RedactionMoved, RedactionUnchecked) as failed:
            return _fail(failed.detail)
    print(f"\n  removed {span.text!r}")
    for said in (
        [notice.detail for notice in saved.applied.notices]
        + saved.notices
        + _plain_xmp_notices(plain_xmp_dropped)
        + removal_notices
    ):
        print(f"  {_YELLOW}{words.render(said)}{_OFF}")
    print(f"  {_GREEN}saved{_OFF} {args.out} {_DIM}· checked gone by re-reading it{_OFF}\n")
    return 0


@dataclass(frozen=True, slots=True)
class _Row:
    path: str
    read: bool
    reports: list[FidelityReport] = field(repr=False)
    note: str

    @property
    def rate(self) -> float | None:
        return green_rate(self.reports)


def _report_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("pdfs", nargs="+")


def _cmd_report(args: argparse.Namespace) -> int:
    rows = [_row_of(path) for path in args.pdfs]
    print()
    for row in rows:
        print(_line_of(row))
    reports = [report for row in rows for report in row.reports]
    overall = green_rate(reports)
    # Every file unreadable or without text: no rate, which a script must see as a failure.
    if overall is None:
        return 1
    color = _rate_color(overall)
    print(f"\n  {color}{overall:.0%}{_OFF} of {len(reports)} spans keep the original font\n")
    return 0


def _row_of(path: str) -> _Row:
    try:
        reports = result_of(partial(_assessed, path))
    except Problem as exc:
        return _Row(path, read=False, reports=[], note=exc.detail)
    except Exception as exc:  # noqa: BLE001 (one bad file must not stop the run)
        return _Row(path, read=False, reports=[], note=str(exc)[:_NAME_COL_WIDTH])
    return _Row(path, read=True, reports=reports, note=f"{len(reports)} spans")


def _assessed(path: str) -> list[FidelityReport]:
    with _opened(path) as engine:
        return engine.assess(engine.index())


def _line_of(row: _Row) -> str:
    name = posixpath.basename(row.path)[:_NAME_COL_WIDTH]
    name_and_note = f"{name:<{_NAME_COL_PAD}}{_DIM}{row.note}{_OFF}"
    if not row.read:
        return f"  {_RED}failed{_OFF}   {name_and_note}"
    if row.rate is None:
        return f"  no text  {name_and_note}"
    return f"  {_rate_color(row.rate)}{row.rate:>5.0%}{_OFF}    {name_and_note}"


def _rate_color(rate: float) -> str:
    if rate >= GREEN_RATE_TARGET:
        return _GREEN
    if rate >= GREEN_RATE_WARN:
        return _YELLOW
    return _RED


def _fixture_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("out", nargs="?", default=_SAMPLE, type=_new_file)
    command.add_argument("--pages", type=int, help="a long contract of this many pages")


def _fixture_refusal(args: argparse.Namespace) -> str | None:
    # Others read `_SAMPLE`, so a long contract never replaces it.
    if args.pages is not None and args.out == _SAMPLE:
        return f"--pages would replace {_SAMPLE}; name another file"
    return None


def _cmd_fixture(args: argparse.Namespace) -> int:
    if args.pages is not None:
        write_dense(args.out, pages=args.pages)
        print(f"  wrote {args.out} {_DIM}· {args.pages} full pages, fonts not embedded{_OFF}")
        return 0
    write_sample(args.out)
    print(f"  wrote {args.out} {_DIM}· page 1 not embedded, page 2 embedded{_OFF}")
    return 0


def _terms_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("file", nargs="?", type=Path, default=named_terms_file())


def _cmd_terms(args: argparse.Namespace) -> int:
    if args.file is None:
        return _fail(words.render(Message("terms_none_named")))
    try:
        terms = terms_in(args.file)
    except TermsUnreadable as refused:
        return _fail(str(refused), hint="(the app won't start on it)")
    print(f"  {terms['title']}")
    if terms["last_changed"] is not None:
        print(f"  {_DIM}{terms['last_changed']}{_OFF}")
    for paragraph in terms["paragraphs"]:
        print(f"    {paragraph['lead']}")
    print(f"  {_GREEN}the server can show these terms{_OFF}")
    return 0


def _invite_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("email")
    command.add_argument(
        "--unlimited",
        action="store_true",
        help="no document limit, for friends and family; invite again without it to end that",
    )


def _cmd_invite(args: argparse.Namespace) -> int:
    users = named_users()
    if users is None:
        return _fail(words.render(Message("no_database")))
    email = checked_email(args.email)
    try:
        secret = named_secret()
    except AccountsUnset as unset:
        return _fail(words.render(Message("no_secret", {"email": email})), hint=f"({unset})")
    user = users.invite(email, now=int(time.time()), is_unlimited=args.unlimited, secret=secret)
    said = "invited_unlimited" if user.is_unlimited else "invited"
    print(f"  {_GREEN}{words.render(Message(said, {'email': user.email}))}{_OFF}")
    return 0


def _remove_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("email")


def _cmd_remove(args: argparse.Namespace) -> int:
    users = named_users()
    if users is None:
        return _fail(words.render(Message("no_database")))
    email = checked_email(args.email)
    try:
        secret = named_secret()
    except AccountsUnset as unset:
        return _fail(words.render(Message("no_secret", {"email": email})), hint=f"({unset})")
    try:
        removal = remove_account(users, email, secret=secret, billing_here=_billing_here)
    except PaymentsUnavailable as unasked:
        return _fail(unasked.detail)
    except AccountsUnset as unset:
        said = words.render(Message("billing_unchecked", {"email": email}))
        return _fail(said, hint=f"({unset})")
    # A typo leaves the person still in: said, so it's caught.
    if removal == "no_account":
        return _fail(words.render(Message("no_account", {"email": email})))
    # Gone, they'd still be charged: their subscription is cancelled in Stripe first.
    if removal == "paying":
        return _fail(words.render(Message("still_paying", {"email": email})))
    if removal == "unchecked":
        return _fail(words.render(Message("billing_unchecked", {"email": email})))
    if removal == "removed":
        print(f"  {_GREEN}{words.render(Message('removed', {'email': email}))}{_OFF}")
        return 0
    assert_never(removal)


def _billing_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("email")
    command.add_argument(
        "--resync",
        action="store_true",
        help="ask Stripe again, as when an event was lost, and keep its answer in the ledger",
    )


def _cmd_billing(args: argparse.Namespace) -> int:
    users = named_users()
    if users is None:
        return _fail(words.render(Message("no_database")))
    email = checked_email(args.email)
    user = users.find(email)
    if user is None:
        return _fail(words.render(Message("billing_no_account", {"email": email})))
    if args.resync:
        refused = _resynced(users, user)
        if refused is not None:
            return _fail(refused)
        user = users.find(email) or user
    until = user.paid_until if user.is_paid_at(int(time.time())) else None
    said = (
        Message("billing_unpaid", {"email": email})
        if until is None
        else Message("billing_paid", {"email": email, "until": _when(until)})
    )
    print(f"  {words.render(said)}")
    if user.stripe_customer is not None:
        print(f"  {_DIM}{user.stripe_customer}{_OFF}")
    for event in users.ledger_of(user.id):
        print(f"    {_ledger_row(event)}")
    return 0


def _resynced(users: UserStore, user: User) -> str | None:
    if user.stripe_customer is None:
        return words.render(Message("billing_no_customer", {"email": user.email}))
    unchecked = words.render(Message("billing_unchecked", {"email": user.email}))
    try:
        billing = _billing_here()
    except AccountsUnset as unset:
        return f"{unchecked} ({unset})"
    if billing is None:
        return unchecked
    try:
        resync(users, billing, user, customer=user.stripe_customer)
    except PaymentsUnavailable as unasked:
        return unasked.detail
    return None


def _billing_here() -> Billing | None:
    accounts = deployed_accounts()
    return None if accounts is None else accounts.billing


def _ledger_row(event: BillingEvent) -> str:
    set_to = "-" if event.paid_until is None else _when(event.paid_until)
    made = _when(event.created)
    return f"{made}  {event.type:<34} {event.outcome:<10} {set_to}  {event.id}"


def _when(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _fail(said: str, *, hint: str = "") -> int:
    dim, red, _green, _yellow, off = _colors_of(sys.stderr)
    shown_hint = f" {dim}{hint}{off}" if hint else ""
    print(f"  {red}{said}{off}{shown_hint}", file=sys.stderr)
    return 1


def _no_span(span_id: str) -> int:
    hint = f"({span_id}: run `squidpdf spans` to list them)"
    return _fail(words.sentence("no_span"), hint=hint)


def _existing_file(path: str) -> str:
    if not Path(path).is_file():
        raise argparse.ArgumentTypeError(f"there's no file at {path}")
    return path


def _one_line(text: str) -> str:
    try:
        check_text(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def _new_file(path: str) -> str:
    target = Path(path)
    if target.is_dir():
        raise argparse.ArgumentTypeError(f"{path} is a folder, not a file")
    if not target.parent.is_dir():
        folder, name = target.parent, target.name
        raise argparse.ArgumentTypeError(f"there's no folder {folder} to save {name} in")
    return path


def _no_refusal(_args: argparse.Namespace) -> None:
    pass


@dataclass(frozen=True, slots=True)
class _Command:
    name: str
    summary: str
    arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]
    refusal: Callable[[argparse.Namespace], str | None] = _no_refusal


_COMMANDS = (
    _Command("spans", "list editable text and how it would edit", _spans_arguments, _cmd_spans),
    _Command("check", "what would happen if you typed this", _check_arguments, _cmd_check),
    _Command(
        "edit",
        "replace a span and save",
        _edit_arguments,
        _cmd_edit,
        refusal=_overwrite_refusal,
    ),
    _Command(
        "redact",
        "remove a span and verify it is gone",
        _redact_arguments,
        _cmd_redact,
        refusal=_overwrite_refusal,
    ),
    _Command("report", "green rate across a corpus", _report_arguments, _cmd_report),
    _Command(
        "fixture",
        "write a sample PDF to try",
        _fixture_arguments,
        _cmd_fixture,
        refusal=_fixture_refusal,
    ),
    _Command(
        "terms", "check a terms file as the server reads it", _terms_arguments, _cmd_terms
    ),
    _Command("invite", "let someone sign in, limited or not", _invite_arguments, _cmd_invite),
    _Command("remove", "take someone's account off", _remove_arguments, _cmd_remove),
    _Command("billing", "what Stripe said of an account", _billing_arguments, _cmd_billing),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squidpdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="cmd", required=True)
    for each in _COMMANDS:
        command = commands.add_parser(each.name, help=each.summary)
        each.arguments(command)
        command.set_defaults(fn=each.run, refusal=each.refusal)
    return parser


def _run(args: argparse.Namespace) -> int:
    try:
        # As a worker runs it: a failure inside MuPDF comes back as the Problem it means.
        return result_of(partial(args.fn, args))
    except _UnknownSpan as unknown:
        return _no_span(unknown.span_id)
    except _NotSaved as failed:
        return _fail(
            words.render(Message("not_saved", {"path": failed.path, "why": failed.why}))
        )
    except Problem as exc:
        return _fail(exc.detail)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    refusal = args.refusal(args)
    if refusal is not None:
        parser.error(refusal)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
