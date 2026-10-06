"""Command line for the engine.

squidpdf spans   file.pdf              what is editable, and how it would edit
squidpdf check   file.pdf ID "text"    what would happen if you typed that
squidpdf edit    file.pdf ID "text" -o out.pdf [--strategy shrink]
squidpdf redact  file.pdf ID -o out.pdf
squidpdf report  file.pdf [...]        the one number that matters
squidpdf fixture out.pdf               a sample document to try it on
squidpdf fixture out.pdf --pages N     a long contract, for timing the browser
"""

# Speaks English only: everything it tells a person goes through core/app/words/.

from __future__ import annotations

import argparse
import os
import posixpath
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO, get_args

from squidpdf.core import (
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Engine,
    Fidelity,
    FidelityReport,
    FontSources,
    Problem,
    Span,
    SpanIndex,
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
    Strategy,
    replace_fit,
    save_edited,
)
from squidpdf.editing.edits import check_text

# A terminal's colours: dim, red, green, yellow, and back to plain.
_COLOURS = ("\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def _colours_of(stream: TextIO) -> tuple[str, ...]:
    """`_COLOURS` when `stream` is a terminal and NO_COLOR isn't set; blanks otherwise."""
    coloured = stream.isatty() and "NO_COLOR" not in os.environ
    return _COLOURS if coloured else ("",) * len(_COLOURS)


# Colours for stdout; a failure, on stderr, gets its own in `_fail`.
_DIM, _RED, _GREEN, _YELLOW, _OFF = _colours_of(sys.stdout)

# How each judgement is marked in `spans`, padded to one width.
_MARKS: dict[Fidelity, str] = {
    "exact": f"{_GREEN}exact{_OFF}      ",
    "approximate": f"{_YELLOW}approximate{_OFF}",
    "substitute": f"{_YELLOW}substitute{_OFF} ",
}

_TEXT_PREVIEW_LEN = 43  # characters of span text shown before truncating with "..."
_NAME_COL_WIDTH = 38  # characters of a file path/name shown before truncating
_NAME_COL_PAD = 40  # column width the (possibly truncated) name is padded to
# The committed sample: the browser's end-to-end test and deploy/check.sh read it.
_SAMPLE = "fixtures/sample.pdf"


def _opened(pdf: str) -> Engine:
    """`pdf`, open for editing, with Google's copies of its fonts to lend, downloaded."""
    return open_pdf(pdf, sources=FontSources(google=google_fonts()))


def _cmd_spans(args: argparse.Namespace) -> int:
    """List every editable span and whether it would keep its own font."""
    with _opened(args.pdf) as engine:
        index = engine.index()
        reports = {report.span_id: report for report in engine.assess(index)}

        # `--page` counts from 1, as the listing prints it.
        shown = [s for s in index if args.page is None or s.page == args.page - 1]
        for span in shown:
            report = reports[span.id]
            mark = _MARKS[report.state]
            note = f" -> {report.substitute}" if report.substitute else ""
            fragments = f" {_DIM}({len(span.fragments)} fragments){_OFF}" if span.merged else ""
            fits = len(span.text) <= _TEXT_PREVIEW_LEN + len("...")
            preview = span.text if fits else span.text[:_TEXT_PREVIEW_LEN] + "..."
            print(
                f"  {span.id}  p{span.page + 1}  {mark}  "
                f"{_DIM}{span.font}{note} {span.size}pt{_OFF}{fragments}\n"
                f"           {preview}"
            )
            # Its own font draws it, but not as it looks: say how.
            if report.state == "approximate" and report.why is not None:
                print(f"           {_DIM}{words.render(report.why)}{_OFF}")
        _summary([reports[span.id] for span in shown])
    return 0


def _summary(reports: list[FidelityReport]) -> None:
    """Print the counts and green rate for one document."""
    rate = green_rate(reports)
    counts = Counter(report.state for report in reports)
    tally = (
        f"\n  {len(reports)} spans · {counts['exact']} exact"
        f" · {counts['approximate']} approximate"
        f" · {counts['substitute']} substitute"
    )
    # No text, so no green rate.
    if rate is None:
        print(f"{tally} · no text")
        return
    print(f"{tally} · {_rate_colour(rate)}{rate:.0%} keep the original font{_OFF}")


class _UnknownSpan(Exception):
    """The PDF has no span by the id typed."""

    def __init__(self, span_id: str) -> None:
        """Name the id typed."""
        super().__init__(span_id)
        self.span_id = span_id


@dataclass(frozen=True, slots=True, eq=False)
class _Opened:
    """A PDF open in the engine, its spans, and the one a command was given."""

    engine: Engine
    index: SpanIndex
    span: Span


@contextmanager
def _opened_at(pdf: str, span_id: str) -> Iterator[_Opened]:
    """The PDF open for as long as the `with` lasts, at the span `span_id` names.

    Raises _UnknownSpan, which `main` says as a hint, if it has no such span.
    """
    with _opened(pdf) as engine:
        index = engine.index()
        span = index.get(span_id)
        if span is None:
            raise _UnknownSpan(span_id)
        yield _Opened(engine, index, span)


def _room_of(opened: _Opened) -> float:
    """The room the span has on its line, as the app works it out."""
    span = opened.span
    return opened.engine.rooms(opened.index, [span])[span.id]


def _cmd_check(args: argparse.Namespace) -> int:
    """Show what would happen if this span became this text, without saving."""
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        fit = replace_fit(opened.engine, span, args.text, room_pt=_room_of(opened)).report
        print(f"\n  {span.text!r} -> {args.text!r}")
        print(f"  {_DIM}{span.font} {span.size}pt{_OFF}")
        print(f"  width {fit.delta_pt:+.2f} pt")

        described = words.render_all(fit.describe())
        # It fits: nothing to choose between.
        if not described:
            print(f"  {_GREEN}fits in place{_OFF}\n")
            return 0
        # It doesn't: say why and list the ways out.
        print(f"  {_RED}{described}{_OFF}")
        for option in fit.options:
            label, detail = words.render(option.label), words.render(option.detail)
            print(f"    {_DIM}{option.name:<9}{_OFF} {label}{_DIM}: {detail}{_OFF}")
        print()
        return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    """Replace a span's text and save. Refuses if it will not fit, unless --force.

    `--strategy` takes a way out `check` offered: shrink or condense, to fit.
    """
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        replace = Replace(span.id, args.text, strategy=args.strategy)
        fit = replace_fit(
            opened.engine,
            span,
            replace.text,
            room_pt=_room_of(opened),
            strategy=replace.strategy,
        ).report
        # A way out that was offered makes a long line fit; missing letters still don't.
        fitted = not fit.missing and fit.strategy != "as-is"
        refused = not (fit.ok or fitted or args.force)
        if refused:
            # `render_all` gives None for no messages, but a fit that isn't ok always has one.
            described = str(words.render_all(fit.describe()))
            return _fail(described, hint="(pass --force to do it anyway)")

        # The same save a download gets.
        saved = save_edited(opened.engine, opened.index, edits=[replace], to=args.out)
    print(f"\n  {span.text!r} -> {args.text!r}")
    for said in [notice.detail for notice in saved.applied.notices] + saved.notices:
        print(f"  {_YELLOW}{words.render(said)}{_OFF}")
    print(f"  {_GREEN}saved{_OFF} {args.out}\n")
    return 0


def _cmd_redact(args: argparse.Namespace) -> int:
    """Remove a span, save, and verify by re-reading the output that it is gone."""
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        # The same save and check a download gets.
        try:
            saved = save_edited(
                opened.engine, opened.index, edits=[Redact(span.id)], to=args.out
            )
        except RedactionFailed as failed:  # the text was still in the file, so none was kept
            return _fail(failed.detail)
    print(f"\n  removed {span.text!r}")
    for said in [notice.detail for notice in saved.applied.notices] + saved.notices:
        print(f"  {_YELLOW}{words.render(said)}{_OFF}")
    print(f"  {_GREEN}saved{_OFF} {args.out} {_DIM}· checked gone by re-reading it{_OFF}\n")
    return 0


@dataclass(frozen=True, slots=True)
class _Row:
    """One file's line in `report`."""

    path: str
    read: bool  # whether it opened and was assessed
    rate: float | None  # its green rate; None if it has no text or wasn't read
    note: str  # how many spans it has, or why it couldn't be read


def _cmd_report(args: argparse.Namespace) -> int:
    """Green rate across a corpus; exits 1 when it could read no file."""
    rows: list[_Row] = []
    total = exact_count = 0
    for path in args.pdfs:
        try:
            with _opened(path) as engine:
                reports = engine.assess(engine.index())
        except Problem as exc:  # damaged or password-protected: says which
            rows.append(_Row(path, read=False, rate=None, note=exc.detail))
            continue
        except Exception as exc:  # noqa: BLE001 (one bad file must not stop the run)
            rows.append(_Row(path, read=False, rate=None, note=str(exc)[:_NAME_COL_WIDTH]))
            continue
        exact_count += sum(1 for report in reports if report.state == "exact")
        total += len(reports)
        rate = green_rate(reports)
        rows.append(_Row(path, read=True, rate=rate, note=f"{len(reports)} spans"))

    print()
    for row in rows:
        print(_line_of(row))
    if total:
        overall = exact_count / total
        colour = _rate_colour(overall)
        print(f"\n  {colour}{overall:.0%}{_OFF} of {total} spans keep the original font\n")
    read_any = any(row.read for row in rows)
    return 0 if read_any else 1


def _line_of(row: _Row) -> str:
    """A file's line in `report`: its green rate, its name, and a note."""
    name = posixpath.basename(row.path)[:_NAME_COL_WIDTH]
    name_and_note = f"{name:<{_NAME_COL_PAD}}{_DIM}{row.note}{_OFF}"
    # The file couldn't be read: `note` says why.
    if not row.read:
        return f"  {_RED}failed{_OFF}   {name_and_note}"
    # No text, so no green rate.
    if row.rate is None:
        return f"  no text  {name_and_note}"
    return f"  {_rate_colour(row.rate)}{row.rate:>5.0%}{_OFF}    {name_and_note}"


def _rate_colour(rate: float) -> str:
    """Green at the target, yellow down to the warning line, red below it."""
    if rate >= GREEN_RATE_TARGET:
        return _GREEN
    if rate >= GREEN_RATE_WARN:
        return _YELLOW
    return _RED


def _cmd_fixture(args: argparse.Namespace) -> int:
    """Two pages: one whose fonts are only referenced, one where they are embedded.

    With `--pages`, a long contract of made-up clauses instead, every page full.
    """
    if args.pages is not None:
        write_dense(args.out, pages=args.pages)
        print(f"  wrote {args.out} {_DIM}· {args.pages} full pages, fonts not embedded{_OFF}")
        return 0
    write_sample(args.out)
    print(f"  wrote {args.out} {_DIM}· page 1 not embedded, page 2 embedded{_OFF}")
    return 0


def _fail(said: str, *, hint: str = "") -> int:
    """Show a failure on stderr, coloured only if stderr is a terminal; return the exit code."""
    dim, red, _green, _yellow, off = _colours_of(sys.stderr)
    shown_hint = f" {dim}{hint}{off}" if hint else ""
    print(f"  {red}{said}{off}{shown_hint}", file=sys.stderr)
    return 1


def _no_span(span_id: str) -> int:
    """Print the standard error for an unknown span id and return the exit code."""
    hint = f"({span_id}: run `squidpdf spans` to list them)"
    return _fail(words.sentence("no_span"), hint=hint)


def _existing_file(path: str) -> str:
    """A path to a file that's there; otherwise a usage error that names it."""
    if not Path(path).is_file():
        raise argparse.ArgumentTypeError(f"there's no file at {path}")
    return path


def _one_line(text: str) -> str:
    """New text, as one line of letters; otherwise a usage error that says why."""
    try:
        check_text(text)
    except ValueError as exc:  # check_text: a character that can't stand in one line
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def _new_file(path: str) -> str:
    """A path to save to: in a folder that's there, and not a folder itself."""
    target = Path(path)
    if target.is_dir():
        raise argparse.ArgumentTypeError(f"{path} is a folder, not a file")
    if not target.parent.is_dir():
        folder, name = target.parent, target.name
        raise argparse.ArgumentTypeError(f"there's no folder {folder} to save {name} in")
    return path


def _same_file(pdf: str, out: str) -> bool:
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
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("-p", "--page", type=int, default=None, help="only this page, from 1")
    command.set_defaults(fn=_cmd_spans)

    command = commands.add_parser("check", help="what would happen if you typed this")
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=_one_line)
    command.set_defaults(fn=_cmd_check)

    command = commands.add_parser("edit", help="replace a span and save")
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=_one_line)
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)
    command.add_argument("--force", action="store_true", help="edit even if it will not fit")
    command.add_argument(
        "--strategy",
        choices=get_args(Strategy.__value__),
        default="as-is",
        help="a way out `check` offers: shrink or condense, to fit",
    )
    command.set_defaults(fn=_cmd_edit)

    command = commands.add_parser("redact", help="remove a span and verify it is gone")
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)
    command.set_defaults(fn=_cmd_redact)

    # No check here: one file that won't open is a row in the report, not the end of it.
    command = commands.add_parser("report", help="green rate across a corpus")
    command.add_argument("pdfs", nargs="+")
    command.set_defaults(fn=_cmd_report)

    command = commands.add_parser("fixture", help="write a sample PDF to try")
    command.add_argument("out", nargs="?", default=_SAMPLE, type=_new_file)
    command.add_argument("--pages", type=int, help="a long contract of this many pages")
    command.set_defaults(fn=_cmd_fixture)

    args = parser.parse_args(argv)
    # Saving over the PDF being read would lose the original if anything went wrong.
    overwrites = args.cmd in ("edit", "redact") and _same_file(args.pdf, args.out)
    if overwrites:
        parser.error(f"-o {args.out} is the PDF being read; save to a new file")
    # Others read `_SAMPLE`, so a long contract never replaces it.
    if args.cmd == "fixture" and args.pages is not None and args.out == _SAMPLE:
        parser.error(f"--pages would replace {_SAMPLE}; name another file")
    try:
        # As a worker runs it: a failure inside MuPDF comes back as the Problem it means.
        return result_of(partial(args.fn, args))
    except _UnknownSpan as unknown:  # the span id typed isn't in the PDF
        return _no_span(unknown.span_id)
    except Problem as exc:  # e.g. the one PDF a command was given won't open
        return _fail(exc.detail)


if __name__ == "__main__":
    sys.exit(main())
