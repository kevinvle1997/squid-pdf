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
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TextIO, get_args

from squidpdf.core import (
    DEFAULT_DRIVER_FACTORY,
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Engine,
    Fidelity,
    FidelityReport,
    FontSources,
    Message,
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
    check_text,
    replace_fit,
    save_edited,
)

# A terminal's colours: dim, red, green, yellow, and back to plain.
_COLORS = ("\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")


def _colors_of(stream: TextIO) -> tuple[str, ...]:
    """`_COLORS` when `stream` is a terminal and NO_COLOR isn't set; blanks otherwise."""
    colored = stream.isatty() and "NO_COLOR" not in os.environ
    return _COLORS if colored else ("",) * len(_COLORS)


# Colours for stdout; a failure, on stderr, gets its own in `_fail`.
_DIM, _RED, _GREEN, _YELLOW, _OFF = _colors_of(sys.stdout)

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


def _spans_arguments(command: argparse.ArgumentParser) -> None:
    """`spans`: the PDF, and a page to list alone."""
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("-p", "--page", type=int, default=None, help="only this page, from 1")


def _cmd_spans(args: argparse.Namespace) -> int:
    """List every editable span and whether it would keep its own font."""
    with _opened(args.pdf) as engine:
        index = engine.index()
        reports = {report.span_id: report for report in engine.assess(index)}

        # `--page` counts from 1, as the listing prints it.
        shown = [s for s in index if args.page is None or s.page == args.page - 1]
        for span in shown:
            print(_listing_of(span, reports[span.id]))
        _summary([reports[span.id] for span in shown])
    return 0


def _listing_of(span: Span, report: FidelityReport) -> str:
    """A span's lines in `spans`: its id, mark and font, its text, and how it differs."""
    note = f" -> {report.substitute}" if report.substitute else ""
    fragments = f" {_DIM}({len(span.fragments)} fragments){_OFF}" if span.merged else ""
    fits = len(span.text) <= _TEXT_PREVIEW_LEN + len("...")
    preview = span.text if fits else span.text[:_TEXT_PREVIEW_LEN] + "..."
    lines = [
        f"  {span.id}  p{span.page + 1}  {_MARKS[report.state]}  "
        f"{_DIM}{span.font}{note} {span.size}pt{_OFF}{fragments}",
        f"           {preview}",
    ]
    # Its own font draws it, but not as it looks: say how.
    if report.state == "approximate" and report.why is not None:
        lines.append(f"           {_DIM}{words.render(report.why)}{_OFF}")
    return "\n".join(lines)


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
    print(f"{tally} · {_rate_color(rate)}{rate:.0%} keep the original font{_OFF}")


class _UnknownSpan(Exception):
    """The PDF has no span by the id typed."""

    def __init__(self, span_id: str) -> None:
        """Name the id typed."""
        super().__init__(span_id)
        self.span_id = span_id


class _NotSaved(Exception):
    """The file was made, but couldn't be put where the command was told to save it."""

    def __init__(self, path: str, why: str) -> None:
        """Name the path, and why, as the system says it."""
        super().__init__(path)
        self.path = path
        self.why = why


@dataclass(frozen=True, slots=True, eq=False)
class _Opened:
    """A PDF open in the engine, its spans, the one a command was given, and its room."""

    engine: Engine
    index: SpanIndex
    span: Span
    rooms: dict[str, float]  # the span's room, by its id, as the app works it out


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
        yield _Opened(engine, index, span, rooms=engine.rooms(index, [span]))


@contextmanager
def _saved_apart(out: str) -> Iterator[str]:
    """A path to save to apart from `out`, its file put at `out` once the save and checks pass.

    So a failed save or check leaves `out` as it was. Raises _NotSaved if `out` can't be had.
    """
    with tempfile.TemporaryDirectory() as scratch:
        saved = Path(scratch) / "saved.pdf"
        yield str(saved)
        _put_at(saved, out)


def _put_at(saved: Path, out: str) -> None:
    """Put the file at `saved` at `out` whole: copied beside it, then moved in at once."""
    try:
        # A folder left behind isn't worth calling a saved file unsaved.
        with tempfile.TemporaryDirectory(
            dir=Path(out).parent, ignore_cleanup_errors=True
        ) as beside:
            staged = Path(beside) / "saved.pdf"
            shutil.copyfile(saved, staged)
            staged.replace(out)
    except OSError as failed:  # no permission, no room, a read-only disk: the system says which
        raise _NotSaved(out, failed.strerror or str(failed)) from None


def _check_arguments(command: argparse.ArgumentParser) -> None:
    """`check`: the PDF, the span, and the text it would become."""
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("text", type=_one_line)


def _cmd_check(args: argparse.Namespace) -> int:
    """Show what would happen if this span became this text, without saving."""
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        fit = replace_fit(opened.engine, span, args.text, room_pt=opened.rooms[span.id]).report
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


def _edit_arguments(command: argparse.ArgumentParser) -> None:
    """`edit`: what `check` takes, where to save, and a way out to take."""
    _check_arguments(command)
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)
    command.add_argument("--force", action="store_true", help="edit even if it will not fit")
    command.add_argument(
        "--strategy",
        choices=get_args(Strategy.__value__),
        default="as-is",
        help="a way out `check` offers: shrink or condense, to fit",
    )


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

        # The same save a download gets.
        with _saved_apart(args.out) as to:
            saved = save_edited(
                opened.engine,
                opened.index,
                rooms=opened.rooms,
                edits=[replace],
                to=to,
                driver_factory=DEFAULT_DRIVER_FACTORY,
            )
    print(f"\n  {span.text!r} -> {args.text!r}")
    for said in [notice.detail for notice in saved.applied.notices] + saved.notices:
        print(f"  {_YELLOW}{words.render(said)}{_OFF}")
    print(f"  {_GREEN}saved{_OFF} {args.out}\n")
    return 0


def _redact_arguments(command: argparse.ArgumentParser) -> None:
    """`redact`: the PDF, the span, and where to save."""
    command.add_argument("pdf", type=_existing_file)
    command.add_argument("span_id")
    command.add_argument("-o", "--out", default="out.pdf", type=_new_file)


def _cmd_redact(args: argparse.Namespace) -> int:
    """Remove a span, save, and verify by re-reading the output that it is gone."""
    with _opened_at(args.pdf, args.span_id) as opened:
        span = opened.span
        # The same save and check a download gets.
        try:
            with _saved_apart(args.out) as to:
                saved = save_edited(
                    opened.engine,
                    opened.index,
                    rooms=opened.rooms,
                    edits=[Redact(span.id)],
                    to=to,
                    driver_factory=DEFAULT_DRIVER_FACTORY,
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
    reports: list[FidelityReport] = field(repr=False)  # each span's; none if it wasn't read
    note: str  # how many spans it has, or why it couldn't be read

    @property
    def rate(self) -> float | None:
        """Its green rate; None if it has no text or wasn't read."""
        return green_rate(self.reports)


def _report_arguments(command: argparse.ArgumentParser) -> None:
    """`report`: the PDFs, unchecked: one that won't open is a row, not the end of the run."""
    command.add_argument("pdfs", nargs="+")


def _cmd_report(args: argparse.Namespace) -> int:
    """Green rate across a corpus; exits 1 when it measured no file."""
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
    """One file's line in `report`: its spans assessed, or why it couldn't be read."""
    try:
        with _opened(path) as engine:
            reports = engine.assess(engine.index())
    except Problem as exc:  # damaged or password-protected: says which
        return _Row(path, read=False, reports=[], note=exc.detail)
    except Exception as exc:  # noqa: BLE001 (one bad file must not stop the run)
        return _Row(path, read=False, reports=[], note=str(exc)[:_NAME_COL_WIDTH])
    return _Row(path, read=True, reports=reports, note=f"{len(reports)} spans")


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
    return f"  {_rate_color(row.rate)}{row.rate:>5.0%}{_OFF}    {name_and_note}"


def _rate_color(rate: float) -> str:
    """Green at the target, yellow down to the warning line, red below it."""
    if rate >= GREEN_RATE_TARGET:
        return _GREEN
    if rate >= GREEN_RATE_WARN:
        return _YELLOW
    return _RED


def _fixture_arguments(command: argparse.ArgumentParser) -> None:
    """`fixture`: where to write, and a page count for a long contract."""
    command.add_argument("out", nargs="?", default=_SAMPLE, type=_new_file)
    command.add_argument("--pages", type=int, help="a long contract of this many pages")


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
    dim, red, _green, _yellow, off = _colors_of(sys.stderr)
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


@dataclass(frozen=True, slots=True)
class _Command:
    """A subcommand: its name, its summary, the arguments it takes, and what runs it."""

    name: str
    summary: str  # its line in `--help`
    arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]


# In the order `--help` lists them.
_COMMANDS = (
    _Command("spans", "list editable text and how it would edit", _spans_arguments, _cmd_spans),
    _Command("check", "what would happen if you typed this", _check_arguments, _cmd_check),
    _Command("edit", "replace a span and save", _edit_arguments, _cmd_edit),
    _Command("redact", "remove a span and verify it is gone", _redact_arguments, _cmd_redact),
    _Command("report", "green rate across a corpus", _report_arguments, _cmd_report),
    _Command("fixture", "write a sample PDF to try", _fixture_arguments, _cmd_fixture),
)


def _parser() -> argparse.ArgumentParser:
    """The command line's parser, a subparser per command."""
    parser = argparse.ArgumentParser(
        prog="squidpdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="cmd", required=True)
    for each in _COMMANDS:
        command = commands.add_parser(each.name, help=each.summary)
        each.arguments(command)
        command.set_defaults(fn=each.run)
    return parser


def _refusal_of(args: argparse.Namespace) -> str | None:
    """Why these arguments can't run together; None when they can."""
    # Saving over the PDF being read would lose the original if anything went wrong.
    overwrites = args.cmd in ("edit", "redact") and _same_file(args.pdf, args.out)
    if overwrites:
        return f"-o {args.out} is the PDF being read; save to a new file"
    # Others read `_SAMPLE`, so a long contract never replaces it.
    if args.cmd == "fixture" and args.pages is not None and args.out == _SAMPLE:
        return f"--pages would replace {_SAMPLE}; name another file"
    return None


def _run(args: argparse.Namespace) -> int:
    """Run the chosen command, saying any failure in words; its exit code."""
    try:
        # As a worker runs it: a failure inside MuPDF comes back as the Problem it means.
        return result_of(partial(args.fn, args))
    except _UnknownSpan as unknown:  # the span id typed isn't in the PDF
        return _no_span(unknown.span_id)
    except _NotSaved as failed:  # the file was made, but its path can't be written
        return _fail(
            words.render(Message("not_saved", {"path": failed.path, "why": failed.why}))
        )
    except Problem as exc:  # e.g. the one PDF a command was given won't open
        return _fail(exc.detail)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the chosen subcommand."""
    parser = _parser()
    args = parser.parse_args(argv)
    refusal = _refusal_of(args)
    # Arguments that can't run together: argparse says why, and exits.
    if refusal is not None:
        parser.error(refusal)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
