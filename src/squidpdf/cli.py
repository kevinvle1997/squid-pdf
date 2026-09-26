"""Command line for the engine spike.

squidpdf spans   file.pdf              what is editable, and how it would edit
squidpdf check   file.pdf ID "text"    what would happen if you typed that
squidpdf edit    file.pdf ID "text" -o out.pdf
squidpdf redact  file.pdf ID -o out.pdf
squidpdf report  file.pdf [...]        the one number that matters
squidpdf fixture out.pdf               a sample document to try it on
"""

from __future__ import annotations

import argparse
import posixpath
import sys

from squidpdf.core import (
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Fidelity,
    FidelityReport,
    MuPDFEngine,
    green_rate,
)
from squidpdf.editing import Redact, Replace, apply, check, verify_redactions

DIM, RED, GREEN, YELLOW, OFF = "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"

_TEXT_PREVIEW_LEN = 43  # characters of span text shown before truncating with "..."
_NAME_COL_WIDTH = 38  # characters of a file path/name shown before truncating
_NAME_COL_PAD = 40  # column width the (possibly truncated) name is padded to


def cmd_spans(args: argparse.Namespace) -> int:
    """List every editable span and whether it would keep its own font."""
    with MuPDFEngine(args.pdf) as engine:
        index = engine.index()
        reports = {report.span_id: report for report in engine.assess(index)}

        for span in index:
            on_other_page = args.page is not None and span.page != args.page
            if on_other_page:
                continue
            report = reports[span.id]
            is_exact = report.state is Fidelity.EXACT
            mark = f"{GREEN}exact{OFF}     " if is_exact else f"{YELLOW}substitute{OFF}"
            note = f" -> {report.substitute}" if report.substitute else ""
            fragments = f" {DIM}({len(span.fragments)} fragments){OFF}" if span.merged else ""
            fits = len(span.text) <= _TEXT_PREVIEW_LEN + len("...")
            preview = span.text if fits else span.text[:_TEXT_PREVIEW_LEN] + "..."
            print(
                f"  {span.id}  p{span.page + 1}  {mark}  "
                f"{DIM}{span.font}{note} {span.size}pt{OFF}{fragments}\n"
                f"           {preview}"
            )
        _summary(list(reports.values()))
    return 0


def _summary(reports: list[FidelityReport]) -> None:
    """Print the counts and green rate for one document."""
    rate = green_rate(reports)
    substituted = sum(1 for report in reports if report.state is not Fidelity.EXACT)
    colour = GREEN if rate >= GREEN_RATE_TARGET else YELLOW
    print(
        f"\n  {len(reports)} spans · {len(reports) - substituted} exact"
        f" · {substituted} substitute"
        f" · {colour}{rate:.0%} keep the original font{OFF}"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Show what would happen if this span became this text, without saving."""
    with MuPDFEngine(args.pdf) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        fit = check(engine, span, args.text)
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {DIM}{span.font} {span.size}pt{OFF}")
        print(f"  width {fit.delta_pt:+.2f} pt")

        problem = fit.describe()
        # It fits: nothing to choose between.
        if not problem:
            print(f"  {GREEN}fits in place{OFF}\n")
            return 0
        # It doesn't: say why and list the ways out.
        print(f"  {RED}{problem}{OFF}")
        for option in fit.options:
            print(f"    {DIM}{option.name:<9}{OFF} {option.label}{DIM}: {option.detail}{OFF}")
        print()
        return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Replace a span's text and save. Refuses if it will not fit, unless --force."""
    with MuPDFEngine(args.pdf) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        fit = check(engine, span, args.text)
        refused = not fit.ok and not args.force
        if refused:
            print(f"  {RED}{fit.describe()}{OFF} {DIM}(pass --force to do it anyway){OFF}")
            return 1

        applied = apply(engine, [Replace(span.id, args.text)], index)
        engine.save(args.out)
        print(f"\n  {span.text!r} -> {args.text!r}")
        for notice in applied.notices:
            print(f"  {YELLOW}{notice.detail}{OFF}")
        print(f"  {GREEN}saved{OFF} {args.out}\n")
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    """Remove a span, save, and verify by re-reading the output that it is gone."""
    with MuPDFEngine(args.pdf) as engine:
        index = engine.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        edits = [Redact(span.id)]
        apply(engine, edits, index)
        engine.save(args.out)
        gone = verify_redactions(engine, edits, index)[span.id]

        colour, verdict = (GREEN, "is gone") if gone else (RED, "IS STILL PRESENT")
        print(f"\n  removed {span.text!r}")
        print(f"  {GREEN}saved{OFF} {args.out}")
        print(f"  {colour}text {verdict}{OFF} {DIM}· verified by re-reading the output{OFF}\n")
        return 0 if gone else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Green rate across a corpus. Below 80% the promise inverts into an apology."""
    rows: list[tuple[str, float | None, str]] = []
    total = exact = 0
    for path in args.pdfs:
        try:
            with MuPDFEngine(path) as engine:
                reports = engine.assess(engine.index())
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
        colour = _rate_colour(rate)
        print(f"  {colour}{rate:>5.0%}{OFF}    {name:<{_NAME_COL_PAD}}{DIM}{note}{OFF}")
    if total:
        overall = exact / total
        colour = GREEN if overall >= GREEN_RATE_TARGET else YELLOW
        print(f"\n  {colour}{overall:.0%}{OFF} of {total} spans keep the original font\n")
    return 0


def _rate_colour(rate: float) -> str:
    """Green at the target, yellow down to the warning line, red below it."""
    if rate >= GREEN_RATE_TARGET:
        return GREEN
    if rate >= GREEN_RATE_WARN:
        return YELLOW
    return RED


def cmd_fixture(args: argparse.Namespace) -> int:
    """Two pages: one whose fonts are only referenced, one where they are embedded."""
    import pymupdf

    doc = pymupdf.open()
    # "tibo" and "tiro" are MuPDF's short names for Times Bold and Times Roman,
    # which it never puts in the file.
    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        "This agreement is made on 14 March 2026 between",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 146),
        "Wescott Analytics Ltd and Lindqvist & Rowe LLP.",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 176),
        "The Client shall pay 48,500 per quarter in arrears.",
        fontname="tiro",
        fontsize=11,
    )

    # Embedded then subsetted, the way a real generator leaves it, so only the
    # glyphs this page used survive and typing an accent will fail.
    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text((72, 96), "Schedule 1 - Scope of work", fontname="emb", fontsize=12)
    embedded.insert_text(
        (72, 124),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    doc.subset_fonts(verbose=False)

    doc.save(args.out)
    doc.close()
    print(f"  wrote {args.out} {DIM}· page 1 not embedded, page 2 embedded{OFF}")
    return 0


def _no_span(span_id: str) -> int:
    """Print the standard error for an unknown span id and return the exit code."""
    print(f"  {RED}no span {span_id}{OFF} {DIM}(run `squidpdf spans` to list them){OFF}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the chosen subcommand."""
    parser = argparse.ArgumentParser(
        prog="squidpdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="cmd", required=True)

    command = commands.add_parser("spans", help="list editable text and how it would edit")
    command.add_argument("pdf")
    command.add_argument("-p", "--page", type=int, default=None)
    command.set_defaults(fn=cmd_spans)

    command = commands.add_parser("check", help="what would happen if you typed this")
    command.add_argument("pdf")
    command.add_argument("span_id")
    command.add_argument("text")
    command.set_defaults(fn=cmd_check)

    command = commands.add_parser("edit", help="replace a span and save")
    command.add_argument("pdf")
    command.add_argument("span_id")
    command.add_argument("text")
    command.add_argument("-o", "--out", default="out.pdf")
    command.add_argument("--force", action="store_true", help="edit even if it will not fit")
    command.set_defaults(fn=cmd_edit)

    command = commands.add_parser("redact", help="remove a span and verify it is gone")
    command.add_argument("pdf")
    command.add_argument("span_id")
    command.add_argument("-o", "--out", default="out.pdf")
    command.set_defaults(fn=cmd_redact)

    command = commands.add_parser("report", help="green rate across a corpus")
    command.add_argument("pdfs", nargs="+")
    command.set_defaults(fn=cmd_report)

    command = commands.add_parser("fixture", help="write a sample PDF to try")
    command.add_argument("out", nargs="?", default="fixtures/sample.pdf")
    command.set_defaults(fn=cmd_fixture)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
