"""The terms file a deployment names, read as the page shows it, or refused with where and why.

A title after "# ", maybe a line under it saying when the terms last changed, then paragraphs
a blank line apart, each opening with its lead in bold. An address after a lead becomes a link.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from squidpdf.core import Message, words
from squidpdf.terms.types import Terms, TermsParagraph, TermsPiece

# The file the deployment's terms are in (env.example); unset or empty, it has none.
_TERMS_FILE = "SQUIDPDF_TERMS"
_TITLE = "# "  # what opens the title's line, as Markdown opens a heading
_MOST_TITLE_LINES = 2  # the title, and the line saying when the terms last changed
# An ATX heading's closing #s, which Markdown drops: # Terms # is titled Terms.
_CLOSING_HASHES = re.compile(r"\s+#+$")
# A paragraph's first line: its lead in bold, then the rest. **Who runs this.** squid-pdf at ...
_LEAD = re.compile(r"\*\*(?P<lead>[^*]*)\*\*(?P<rest>.*)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True, slots=True)
class _Unshowable:
    """Markdown the page can't show as Markdown draws it, and the sentence refusing it."""

    drawn: re.Pattern[str]
    refusal: str  # its sentence's key in core/app/words, given the file and the line


# What opens a line Markdown draws as other than words, each anchored at the line's start.
_QUOTE = _Unshowable(re.compile(r"^>"), "terms_quote")
_RULE = _Unshowable(
    re.compile(r"^(?:=+|-+|(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"), "terms_rule"
)
_HEADING = _Unshowable(re.compile(r"^#{1,6}(?:\s|$)"), "terms_heading")
_TABLE = _Unshowable(re.compile(r"^\||^[-:\s]+\|[-:|\s]*$"), "terms_table")
# First match wins in both: a quotation's > is named before what it quotes; - - - is a rule.
# Where a block starts, a paragraph's first line or the title's next, any number opens a list.
_BLOCK_STARTS = (
    _QUOTE,
    _RULE,
    _Unshowable(re.compile(r"^(?:[-*+]|\d{1,9}[.)])(?:\s|$)"), "terms_list"),
    _HEADING,
    _TABLE,
)
# Inside a paragraph only a bullet, 1. or 1), with words after it, opens one (CommonMark).
_INTERRUPTIONS = (
    _QUOTE,
    _RULE,
    _Unshowable(re.compile(r"^(?:[-*+]|1[.)])\s+\S"), "terms_list"),
    _HEADING,
    _TABLE,
)
# What Markdown draws inside a line. First match wins: a link holds what the rows below look
# for, and an address in < > reads as a tag too.
_IN_LINES = (
    _Unshowable(
        re.compile(r"\[[^\]]*\][(\[]|^\[[^\]]+\]:|<[a-z][a-z0-9+.-]*:", re.IGNORECASE),
        "terms_link",
    ),
    _Unshowable(re.compile(r"<[^<>\s@]+@[^<>\s]+>"), "terms_email_brackets"),
    _Unshowable(re.compile(r"</?[a-z!][^>]*>", re.IGNORECASE), "terms_html"),
    _Unshowable(
        re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);", re.IGNORECASE), "terms_entity"
    ),
    _Unshowable(re.compile(r"\\(?:[!-/:-@\[-`{-~]|$)"), "terms_escape"),
    _Unshowable(re.compile(r"[*`]|~~|(?<!\w)_(?=\S)|(?<=\S)_(?!\w)"), "terms_emphasis"),
)


class TermsUnreadable(Exception):
    """Why the page can't show a terms file, said with the file and the line."""

    def __init__(self, reason: Message) -> None:
        """`reason` names a sentence in `core.app.words`, put into words here."""
        super().__init__(words.render(reason))
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Block:
    """Lines with no blank line between them, and the number of the first, from 1."""

    first: int
    lines: tuple[str, ...]


def named_terms_file() -> Path | None:
    """The file `SQUIDPDF_TERMS` names; None where it names none, as a self-hosted copy's."""
    named = os.environ.get(_TERMS_FILE)  # .get: a self-hosted copy names none
    return Path(named) if named else None


def deployed_terms() -> Terms | None:
    """The terms in the file `SQUIDPDF_TERMS` names; None where it names none."""
    path = named_terms_file()
    return None if path is None else terms_in(path)


def terms_in(path: Path) -> Terms:
    """The terms the file at `path` holds; TermsUnreadable when the page can't show them."""
    blocks = _blocks_in(_written_in(path))
    if not blocks:
        raise _refused("terms_no_title", path, 1)
    title_block, *paragraph_blocks = blocks
    title, last_changed = _title_in(title_block, path)
    if not paragraph_blocks:
        raise _refused("terms_no_paragraphs", path, title_block.first)
    paragraphs = [_paragraph_in(block, path) for block in paragraph_blocks]
    _refuse_a_lead_said_twice(paragraphs, paragraph_blocks, path)
    return {"title": title, "last_changed": last_changed, "paragraphs": paragraphs}


def _refused(key: str, path: Path, line: int) -> TermsUnreadable:
    """The refusal the sentence `key` says of line `line` of the file at `path`."""
    return TermsUnreadable(Message(key, {"path": str(path), "line": line}))


def _written_in(path: Path) -> str:
    """The file's text, a byte-order mark left out, as Notepad saves one."""
    try:
        written = path.read_bytes()
    except FileNotFoundError:  # read_bytes: nothing at the path
        raise TermsUnreadable(Message("terms_missing", {"path": str(path)})) from None
    except IsADirectoryError:  # read_bytes: a folder, as Docker makes at a missing path
        raise TermsUnreadable(Message("terms_folder", {"path": str(path)})) from None
    except PermissionError:  # read_bytes: the app's user may not read it
        raise TermsUnreadable(Message("terms_not_permitted", {"path": str(path)})) from None
    except OSError as failed:  # read_bytes: a file where a folder should be, or the disk failed
        why = failed.strerror or str(failed)
        raise TermsUnreadable(
            Message("terms_unreadable", {"path": str(path), "why": why})
        ) from None
    try:
        return written.decode("utf-8-sig")
    except UnicodeDecodeError as failed:  # decode: bytes that aren't UTF-8, at `start`
        raise _refused(
            "terms_not_utf8", path, written.count(b"\n", 0, failed.start) + 1
        ) from None


def _blocks_in(written: str) -> list[_Block]:
    """The file's runs of lines, split at blank lines."""
    blocks: list[_Block] = []
    lines: list[str] = []
    for number, line in enumerate([*written.splitlines(), ""], start=1):
        if line.strip():
            lines.append(line.strip())
            continue
        # A blank line ends the run before it, if there is one.
        if lines:
            blocks.append(_Block(number - len(lines), tuple(lines)))
        lines = []
    return blocks


def _title_in(block: _Block, path: Path) -> tuple[str, str | None]:
    """The title, and the line under it saying when the terms last changed, if there is one."""
    title_line, *rest = block.lines
    if _QUOTE.drawn.search(title_line):
        raise _refused("terms_quote", path, block.first)
    title = _CLOSING_HASHES.sub("", title_line.removeprefix(_TITLE)).strip()
    if not title_line.startswith(_TITLE) or not title:
        raise _refused("terms_no_title", path, block.first)
    _refuse_formatted(title, path=path, line=block.first, own="terms_title_formatted")
    last_changed = rest[0] if rest else None
    if last_changed is not None:
        _refuse_as_last_changed(last_changed, path=path, line=block.first + 1)
    if len(block.lines) > _MOST_TITLE_LINES:
        raise _refused("terms_title_too_long", path, block.first + _MOST_TITLE_LINES)
    return title, last_changed


def _refuse_as_last_changed(text: str, *, path: Path, line: int) -> None:
    """Refuse a line under the title that isn't plain words saying when the terms changed."""
    drawn = _refusal_in(text, _BLOCK_STARTS)
    # Neither of this line's own ways fixes a quotation or a rule, so each has its own sentence.
    if drawn in (_QUOTE.refusal, _RULE.refusal):
        raise _refused(drawn, path, line)
    # A list or a heading here may be meant as either: this line's own sentence says both ways.
    if drawn is not None:
        raise _refused("terms_date_formatted", path, line)
    _refuse_formatted(text, path=path, line=line, own="terms_date_formatted")


def _refuse_formatted(text: str, *, path: Path, line: int, own: str) -> None:
    """Refuse what Markdown draws inside `text`: bold, italic or code in the `own` sentence."""
    drawn = _refusal_in(text, _IN_LINES)
    if drawn == "terms_emphasis":
        raise _refused(own, path, line)
    if drawn is not None:
        raise _refused(drawn, path, line)


def _paragraph_in(block: _Block, path: Path) -> TermsParagraph:
    """A paragraph's lead, and the rest in pieces, its lines joined by a space."""
    first_line, *continuing = block.lines
    _refuse_drawn(first_line, _BLOCK_STARTS, path=path, line=block.first)
    for number, line in enumerate(continuing, start=block.first + 1):
        _refuse_drawn(line, _INTERRUPTIONS, path=path, line=number)
    opening = _LEAD.fullmatch(first_line)
    if opening is None:
        raise _refused("terms_no_lead", path, block.first)
    lead = opening["lead"].strip()
    if not lead:
        raise _refused("terms_empty_lead", path, block.first)
    after_lead = (opening["rest"], *continuing)
    _refuse_drawn(lead, _IN_LINES, path=path, line=block.first)
    for number, text in enumerate(after_lead, start=block.first):
        _refuse_drawn(text, _IN_LINES, path=path, line=number)
    rest = " ".join(text.strip() for text in after_lead).strip()
    if not rest:
        raise _refused("terms_lead_alone", path, block.first)
    return {"lead": lead, "pieces": list(_pieces_of(rest))}


def _refuse_drawn(
    text: str, unshowable: tuple[_Unshowable, ...], *, path: Path, line: int
) -> None:
    """Refuse what Markdown would draw from `text`, line `line`, by the rows of `unshowable`."""
    drawn = _refusal_in(text, unshowable)
    if drawn is not None:
        raise _refused(drawn, path, line)


def _refusal_in(text: str, unshowable: tuple[_Unshowable, ...]) -> str | None:
    """The key of the sentence refusing what Markdown would draw from `text`, if anything."""
    return next((row.refusal for row in unshowable if row.drawn.search(text)), None)


def _refuse_a_lead_said_twice(
    paragraphs: list[TermsParagraph], blocks: list[_Block], path: Path
) -> None:
    """Refuse a lead a paragraph before already opens with."""
    seen: set[str] = set()
    for paragraph, block in zip(paragraphs, blocks, strict=True):
        if paragraph["lead"] in seen:
            raise TermsUnreadable(
                Message(
                    "terms_repeated_lead",
                    {"path": str(path), "line": block.first, "lead": paragraph["lead"]},
                )
            )
        seen.add(paragraph["lead"])


def _pieces_of(text: str) -> Iterator[TermsPiece]:
    """`text` as words, and the email addresses in it."""
    shown_to = 0
    for address in _EMAIL.finditer(text):
        if address.start() > shown_to:
            yield {"kind": "words", "text": text[shown_to : address.start()]}
        yield {"kind": "email", "address": address[0]}
        shown_to = address.end()
    if shown_to < len(text):
        yield {"kind": "words", "text": text[shown_to:]}
