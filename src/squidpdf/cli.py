"""Command line for the engine.

squidpdf spans   file.pdf              what is editable, and how it would edit
squidpdf check   file.pdf ID "text"    what would happen if you typed that
squidpdf edit    file.pdf ID "text" -o out.pdf
squidpdf redact  file.pdf ID -o out.pdf
squidpdf report  file.pdf [...]        the one number that matters
squidpdf fixture out.pdf               a sample document to try it on
squidpdf fixture out.pdf --pages N     a long contract, for timing the browser
"""

# Speaks English only: everything it tells a person goes through core/words/.

from __future__ import annotations

import argparse
import os
import posixpath
import sys
from functools import partial
from pathlib import Path

from squidpdf.core import (
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Fidelity,
    FidelityReport,
    Problem,
    google_fonts,
    green_rate,
    open_pdf,
    result_of,
    words,
    write_dense,
    write_sample,
)
from squidpdf.editing import (
    Redact,
    RedactionFailed,
    Replace,
    apply_edits,
    replace_fit,
    resolve,
    save_edited,
)
from squidpdf.editing.edits import check_text

__all__ = [
    "cmd_spans",
    "cmd_check",
    "cmd_edit",
    "cmd_redact",
    "cmd_report",
    "cmd_fixture",
    "main",
]

# Colours for a terminal; none when the output is piped or the reader set NO_COLOR.
_COLOURED = sys.stdout.isatty() and "NO_COLOR" not in os.environ
_COLOURS = ("\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")
DIM, RED, GREEN, YELLOW, OFF = _COLOURS if _COLOURED else ("",) * len(_COLOURS)

# How each judgement is marked in `spans`, padded to one width.
_MARKS = {
    Fidelity.EXACT: f"{GREEN}exact{OFF}      ",
    Fidelity.APPROXIMATE: f"{YELLOW}approximate{OFF}",
    Fidelity.SUBSTITUTE: f"{YELLOW}substitute{OFF} ",
}

_TEXT_PREVIEW_LEN = 43  # characters of span text shown before truncating with "..."
_NAME_COL_WIDTH = 38  # characters of a file path/name shown before truncating
_NAME_COL_PAD = 40  # column width the (possibly truncated) name is padded to
_SAMPLE = "fixtures/sample.pdf"  # the committed sample the tests read


def cmd_spans(args: argparse.Namespace) -> int:
    """List every editable span and whether it would keep its own font."""
    with open_pdf(args.pdf, fetch=google_fonts()) as engine:
        index = engine.index()
        reports = {report.span_id: report for report in engine.assess(index)}

        # `--page` counts from 1, as the listing prints it.
        shown = [s for s in index if args.page is None or s.page == args.page - 1]
        for span in shown:
            report = reports[span.id]
            mark = _MARKS[report.state]
            note = f" -> {report.substitute}" if report.substitute else ""
            fragments = f" {DIM}({len(span.fragments)} fragments){OFF}" if span.merged else ""
            fits = len(span.text) <= _TEXT_PREVIEW_LEN + len("...")
            preview = span.text if fits else span.text[:_TEXT_PREVIEW_LEN] + "..."
            print(
                f"  {span.id}  p{span.page + 1}  {mark}  "
                f"{DIM}{span.font}{note} {span.size}pt{OFF}{fragments}\n"
                f"           {preview}"
            )
            # Its own font draws it, but not as it looks: say how.
            if report.state is Fidelity.APPROXIMATE and report.why is not None:
                print(f"           {DIM}{words.render(report.why)}{OFF}")
        summary([reports[span.id] for span in shown])
    return 0


def summary(reports: list[FidelityReport]) -> None:
    """Print the counts and green rate for one document."""
    rate = green_rate(reports)
    count = {state: 0 for state in Fidelity}
    for report in reports:
        count[report.state] += 1
    colour = GREEN if rate >= GREEN_RATE_TARGET else YELLOW
    print(
        f"\n  {len(reports)} spans · {count[Fidelity.EXACT]} exact"
        f" · {count[Fidelity.APPROXIMATE]} approximate"
        f" · {count[Fidelity.SUBSTITUTE]} substitute"
        f" · {colour}{rate:.0%} keep the original font{OFF}"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Show what would happen if this span became this text, without saving."""
    with open_pdf(args.pdf, fetch=google_fonts()) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return no_span(args.span_id)

        fit = replace_fit(engine, span, args.text)
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {DIM}{span.font} {span.size}pt{OFF}")
        print(f"  width {fit.delta_pt:+.2f} pt")

        problem = words.render_all(fit.describe())
        # It fits: nothing to choose between.
        if not problem:
            print(f"  {GREEN}fits in place{OFF}\n")
            return 0
        # It doesn't: say why and list the ways out.
        print(f"  {RED}{problem}{OFF}")
        for option in fit.options:
            label, detail = words.render(option.label), words.render(option.detail)
            print(f"    {DIM}{option.name:<9}{OFF} {label}{DIM}: {detail}{OFF}")
        print()
        return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Replace a span's text and save. Refuses if it will not fit, unless --force."""
    with open_pdf(args.pdf, fetch=google_fonts()) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return no_span(args.span_id)

        fit = replace_fit(engine, span, args.text)
        refused = not fit.ok and not args.force
        if refused:
            problem = words.render_all(fit.describe())
            refusal = f"  {RED}{problem}{OFF} {DIM}(pass --force to do it anyway){OFF}"
            print(refusal, file=sys.stderr)
            return 1

        applied = apply_edits(engine, resolve(engine, [Replace(span.id, args.text)], index))
        saved = engine.save(args.out)
        print(f"\n  {span.text!r} -> {args.text!r}")
        for said in [notice.detail for notice in applied.notices] + saved:
            print(f"  {YELLOW}{words.render(said)}{OFF}")
        print(f"  {GREEN}saved{OFF} {args.out}\n")
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    """Remove a span, save, and verify by re-reading the output that it is gone."""
    with open_pdf(args.pdf, fetch=google_fonts()) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return no_span(args.span_id)

        # The same save and check a download gets.
        try:
            saved = save_edited(engine, index, edits=[Redact(span.id)], to=args.out)
        except RedactionFailed as failed:  # the text was still in the file, so none was kept
            print(f"  {RED}{failed.detail}{OFF}", file=sys.stderr)
            return 1
    print(f"\n  removed {span.text!r}")
    for said in [notice.detail for notice in saved.applied.notices] + saved.notices:
        print(f"  {YELLOW}{words.render(said)}{OFF}")
    print(f"  {GREEN}saved{OFF} {args.out} {DIM}· checked gone by re-reading it{OFF}\n")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Green rate across a corpus. Below 80% the promise inverts into an apology."""
    rows: list[tuple[str, float | None, str]] = []
    total = exact = 0
    for path in args.pdfs:
        try:
            with open_pdf(path, fetch=google_fonts()) as engine:
                reports = engine.assess(engine.index())
        except Problem as exc:  # damaged or password-protected: says which
            rows.append((path, None, exc.detail))
            continue
        except Exception as exc:  # noqa: BLE001 (one bad file must not stop the run)
            rows.append((path, None, str(exc)[:_NAME_COL_WIDTH]))
            continue
        exact += sum(1 for report in reports if report.state is Fidelity.EXACT)
        total += len(reports)
        rows.append((path, green_rate(reports), f"{len(reports)} spans"))

    print()
    for path, rate, note in rows:
        name = posixpath.basename(path)[:_NAME_COL_WIDTH]
        # The file couldn't be read: `note` says why.
        if rate is None:
            print(f"  {RED}failed{OFF}   {name:<{_NAME_COL_PAD}}{DIM}{note}{OFF}")
            continue
        colour = rate_colour(rate)
        print(f"  {colour}{rate:>5.0%}{OFF}    {name:<{_NAME_COL_PAD}}{DIM}{note}{OFF}")
    if total:
        overall = exact / total
        colour = GREEN if overall >= GREEN_RATE_TARGET else YELLOW
        print(f"\n  {colour}{overall:.0%}{OFF} of {total} spans keep the original font\n")
    return 0


def rate_colour(rate: float) -> str:
    """Green at the target, yellow down to the warning line, red below it."""
    if rate >= GREEN_RATE_TARGET:
        return GREEN
    if rate >= GREEN_RATE_WARN:
        return YELLOW
    return RED


def cmd_fixture(args: argparse.Namespace) -> int:
    """Two pages: one whose fonts are only referenced, one where they are embedded.

    With `--pages`, a long contract of made-up clauses instead, every page full.
    """
    if args.pages is not None:
        write_dense(args.out, pages=args.pages)
        print(f"  wrote {args.out} {DIM}· {args.pages} full pages, fonts not embedded{OFF}")
        return 0
    write_sample(args.out)
    print(f"  wrote {args.out} {DIM}· page 1 not embedded, page 2 embedded{OFF}")
    return 0


def no_span(span_id: str) -> int:
    """Print the standard error for an unknown span id and return the exit code."""
    said = words.sentence("no_span")
    hint = f"{DIM}({span_id}: run `squidpdf spans` to list them){OFF}"
    print(f"  {RED}{said}{OFF} {hint}", file=sys.stderr)
    return 1


def existing_file(path: str) -> str:
    """A path to a file that's there; otherwise a usage error that names it."""
    if not Path(path).is_file():
        raise argparse.ArgumentTypeError(f"there's no file at {path}")
    return path


def one_line(text: str) -> str:
    """New text, as one line of letters; otherwise a usage error that says why."""
    try:
        check_text(text)
    except ValueError as exc:  # a line break, a tab, or half an emoji
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def new_file(path: str) -> str:
    """A path to save to: in a folder that's there, and not a folder itself."""
    target = Path(path)
    if target.is_dir():
        raise argparse.ArgumentTypeError(f"{path} is a folder, not a file")
    if not target.parent.is_dir():
        folder, name = target.parent, target.name
        raise argparse.ArgumentTypeError(f"there's no folder {folder} to save {name} in")
    return path


def same_file(pdf: str, out: str) -> bool:
    """Whether `out` is `pdf` itself, under this name or another."""
    return Path(out).exists() and Path(out).samefile(pdf)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the chosen subcommand."""
    parser = argparse.ArgumentParser(
        prog="squidpdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="cmd", required=True)

    command = commands.add_parser("spans", help="list editable text and how it would edit")
    command.add_argument("pdf", type=existing_file)
    command.add_argument("-p", "--page", type=int, default=None, help="only this page, from 1")
    command.set_defaults(fn=cmd_spans)

    command = commands.add_parser("check", help="what would happen if you typed this")
    command.add_argument("pdf", type=existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=one_line)
    command.set_defaults(fn=cmd_check)

    command = commands.add_parser("edit", help="replace a span and save")
    command.add_argument("pdf", type=existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=one_line)
    command.add_argument("-o", "--out", default="out.pdf", type=new_file)
    command.add_argument("--force", action="store_true", help="edit even if it will not fit")
    command.set_defaults(fn=cmd_edit)

    command = commands.add_parser("redact", help="remove a span and verify it is gone")
    command.add_argument("pdf", type=existing_file)
    command.add_argument("span_id")
    command.add_argument("-o", "--out", default="out.pdf", type=new_file)
    command.set_defaults(fn=cmd_redact)

    # No check here: one file that won't open is a row in the report, not the end of it.
    command = commands.add_parser("report", help="green rate across a corpus")
    command.add_argument("pdfs", nargs="+")
    command.set_defaults(fn=cmd_report)

    command = commands.add_parser("fixture", help="write a sample PDF to try")
    command.add_argument("out", nargs="?", default=_SAMPLE, type=new_file)
    command.add_argument("--pages", type=int, help="a long contract of this many pages")
    command.set_defaults(fn=cmd_fixture)

    args = parser.parse_args(argv)
    # Saving over the PDF being read would lose the original if anything went wrong.
    overwrites = args.cmd in ("edit", "redact") and same_file(args.pdf, args.out)
    if overwrites:
        parser.error(f"-o {args.out} is the PDF being read; save to a new file")
    # The tests read the committed sample, so a long contract never lands on it.
    if args.cmd == "fixture" and args.pages is not None and args.out == _SAMPLE:
        parser.error(f"--pages would replace {_SAMPLE}; name another file")
    try:
        # As a worker runs it: a failure inside MuPDF comes back as the Problem it means.
        return result_of(partial(args.fn, args))
    except Problem as exc:  # e.g. the one PDF a command was given won't open
        print(f"  {RED}{exc.detail}{OFF}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
