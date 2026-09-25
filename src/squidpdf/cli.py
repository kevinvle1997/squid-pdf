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
import sys

from squidpdf.core import GREEN_RATE_TARGET, GREEN_RATE_WARN, Fidelity, MuPDFEngine, green_rate
from squidpdf.editing import Redact, Replace, apply, check, verify_redactions

DIM, RED, GREEN, YELLOW, OFF = "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"

_TEXT_PREVIEW_LEN = 43  # characters of span text shown before truncating with "..."
_NAME_COL_WIDTH = 38  # characters of a file path/name shown before truncating
_NAME_COL_PAD = 40  # column width the (possibly truncated) name is padded to


def cmd_spans(args: argparse.Namespace) -> int:
    """List every editable span and whether it would keep its own font."""
    with MuPDFEngine(args.pdf) as eng:
        index = eng.index()
        reports = {r.span_id: r for r in eng.assess(index)}

        for span in index:
            if args.page is not None and span.page != args.page:
                continue
            r = reports[span.id]
            is_exact = r.state is Fidelity.EXACT
            mark = f"{GREEN}exact{OFF}     " if is_exact else f"{YELLOW}substitute{OFF}"
            note = f" -> {r.substitute}" if r.substitute else ""
            frags = f" {DIM}({len(span.fragments)} fragments){OFF}" if span.merged else ""
            text = (
                span.text
                if len(span.text) <= _TEXT_PREVIEW_LEN + 3
                else span.text[:_TEXT_PREVIEW_LEN] + "..."
            )
            print(
                f"  {span.id}  p{span.page + 1}  {mark}  "
                f"{DIM}{span.font}{note} {span.size}pt{OFF}{frags}\n"
                f"           {text}"
            )
        _summary(list(reports.values()))
    return 0


def _summary(reports: list) -> None:
    """Print the counts and green rate for one document."""
    rate = green_rate(reports)
    n_sub = sum(1 for r in reports if r.state is not Fidelity.EXACT)
    colour = GREEN if rate >= GREEN_RATE_TARGET else YELLOW
    print(
        f"\n  {len(reports)} spans · {len(reports) - n_sub} exact · {n_sub} substitute"
        f" · {colour}{rate:.0%} keep the original font{OFF}"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Show what would happen if this span became this text, without saving."""
    with MuPDFEngine(args.pdf) as eng:
        index = eng.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        fit = check(eng, span, args.text)
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {DIM}{span.font} {span.size}pt{OFF}")
        print(f"  width {fit.delta_pt:+.2f} pt")

        problem = fit.describe()
        if problem:
            print(f"  {RED}{problem}{OFF}")
            for opt in fit.options:
                print(f"    {DIM}{opt.name:<9}{OFF} {opt.label}{DIM}: {opt.detail}{OFF}")
            print()
        else:
            print(f"  {GREEN}fits in place{OFF}\n")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Replace a span's text and save. Refuses if it will not fit, unless --force."""
    with MuPDFEngine(args.pdf) as eng:
        index = eng.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        fit = check(eng, span, args.text)
        if not fit.ok and not args.force:
            print(f"  {RED}{fit.describe()}{OFF} {DIM}(pass --force to do it anyway){OFF}")
            return 1

        apply(eng, [Replace(span.id, args.text)], index)
        eng.save(args.out)
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {GREEN}saved{OFF} {args.out}\n")
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    """Remove a span, save, and verify by re-reading the output that it is gone."""
    with MuPDFEngine(args.pdf) as eng:
        index = eng.index()
        span = index.get(args.span_id)
        if span is None:
            return _no_span(args.span_id)

        edits = [Redact(span.id)]
        apply(eng, edits, index)
        eng.save(args.out)
        gone = verify_redactions(eng, edits, index)[span.id]

        print(f"\n  removed {span.text!r}")
        print(f"  {GREEN}saved{OFF} {args.out}")
        print(
            f"  {GREEN if gone else RED}text "
            f"{'is gone' if gone else 'IS STILL PRESENT'}{OFF} "
            f"{DIM}· verified by re-reading the output{OFF}\n"
        )
        return 0 if gone else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Green rate across a corpus. Below 80% the promise inverts into an apology."""
    rows: list[tuple[str, float | None, str]] = []
    total = exact = 0
    for path in args.pdfs:
        try:
            with MuPDFEngine(path) as eng:
                reports = eng.assess(eng.index())
        except Exception as exc:  # noqa: BLE001 (one bad file must not stop the run)
            rows.append((path, None, str(exc)[:_NAME_COL_WIDTH]))
            continue
        n_exact = sum(1 for r in reports if r.state is Fidelity.EXACT)
        exact, total = exact + n_exact, total + len(reports)
        rows.append((path, green_rate(reports), f"{len(reports)} spans"))

    print()
    for path, rate, note in rows:
        name = path.rsplit("/", 1)[-1][:_NAME_COL_WIDTH]
        if rate is None:
            print(f"  {RED}failed{OFF}   {name:<{_NAME_COL_PAD}}{DIM}{note}{OFF}")
        else:
            c = (
                GREEN
                if rate >= GREEN_RATE_TARGET
                else YELLOW
                if rate >= GREEN_RATE_WARN
                else RED
            )
            print(f"  {c}{rate:>5.0%}{OFF}    {name:<{_NAME_COL_PAD}}{DIM}{note}{OFF}")
    if total:
        overall = exact / total
        c = GREEN if overall >= GREEN_RATE_TARGET else YELLOW
        print(f"\n  {c}{overall:.0%}{OFF} of {total} spans keep the original font\n")
    return 0


def cmd_fixture(args: argparse.Namespace) -> int:
    """Two pages: one whose fonts are only referenced, one where they are embedded."""
    import pymupdf

    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    p1.insert_text(
        (72, 128),
        "This agreement is made on 14 March 2026 between",
        fontname="tiro",
        fontsize=11,
    )
    p1.insert_text(
        (72, 146),
        "Wescott Analytics Ltd and Lindqvist & Rowe LLP.",
        fontname="tiro",
        fontsize=11,
    )
    p1.insert_text(
        (72, 176),
        "The Client shall pay 48,500 per quarter in arrears.",
        fontname="tiro",
        fontsize=11,
    )

    # Embedded then subsetted, the way a real generator leaves it, so only the
    # glyphs this page used survive and typing an accent will fail.
    p2 = doc.new_page()
    p2.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    p2.insert_text((72, 96), "Schedule 1 - Scope of work", fontname="emb", fontsize=12)
    p2.insert_text(
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
    ap = argparse.ArgumentParser(
        prog="squidpdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("spans", help="list editable text and how it would edit")
    s.add_argument("pdf")
    s.add_argument("-p", "--page", type=int, default=None)
    s.set_defaults(fn=cmd_spans)

    s = sub.add_parser("check", help="what would happen if you typed this")
    s.add_argument("pdf")
    s.add_argument("span_id")
    s.add_argument("text")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("edit", help="replace a span and save")
    s.add_argument("pdf")
    s.add_argument("span_id")
    s.add_argument("text")
    s.add_argument("-o", "--out", default="out.pdf")
    s.add_argument("--force", action="store_true", help="edit even if it will not fit")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("redact", help="remove a span and verify it is gone")
    s.add_argument("pdf")
    s.add_argument("span_id")
    s.add_argument("-o", "--out", default="out.pdf")
    s.set_defaults(fn=cmd_redact)

    s = sub.add_parser("report", help="green rate across a corpus")
    s.add_argument("pdfs", nargs="+")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("fixture", help="write a sample PDF to try")
    s.add_argument("out", nargs="?", default="fixtures/sample.pdf")
    s.set_defaults(fn=cmd_fixture)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
