"""A title after "# ", maybe a last-changed line, then paragraphs each led in bold."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from squidpdf.core import Message, words
from squidpdf.terms.types import Terms, TermsParagraph, TermsPiece

_TERMS_FILE = "SQUIDPDF_TERMS"
_TITLE = "# "
_MOST_TITLE_LINES = 2
# An ATX heading's closing #s, which Markdown drops: # Terms # is titled Terms.
_CLOSING_HASHES = re.compile(r"\s+#+$")
_LEAD = re.compile(r"\*\*(?P<lead>[^*]*)\*\*(?P<rest>.*)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True, slots=True)
class _Unshowable:
    drawn: re.Pattern[str]
    refusal: str


_QUOTE = _Unshowable(re.compile(r"^>"), "terms_quote")
_RULE = _Unshowable(
    re.compile(r"^(?:=+|-+|(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"), "terms_rule"
)
_HEADING = _Unshowable(re.compile(r"^#{1,6}(?:\s|$)"), "terms_heading")
_TABLE = _Unshowable(re.compile(r"^\||^[-:\s]+\|[-:|\s]*$"), "terms_table")
# First match wins: a quotation's > is named before what it quotes; - - - is a rule.
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
# First match wins: a link holds what the rows below look for; an address in < > reads as a tag.
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


@dataclass(frozen=True, slots=True)
class _ReadFailure:
    raised: type[OSError]
    refusal: str


# First match wins, so OSError, the others' superclass, comes last.
_READ_FAILURES = (
    _ReadFailure(FileNotFoundError, "terms_missing"),
    # A folder, as Docker makes at a missing path.
    _ReadFailure(IsADirectoryError, "terms_folder"),
    _ReadFailure(PermissionError, "terms_not_permitted"),
    _ReadFailure(OSError, "terms_unreadable"),
)


class TermsUnreadable(Exception):
    def __init__(self, reason: Message) -> None:
        super().__init__(words.render(reason))
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Block:
    first: int
    lines: tuple[str, ...]


def named_terms_file() -> Path | None:
    named = os.environ.get(_TERMS_FILE)
    return Path(named) if named else None


def deployed_terms() -> Terms | None:
    path = named_terms_file()
    return None if path is None else terms_in(path)


def terms_in(path: Path) -> Terms:
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
    return TermsUnreadable(Message(key, {"path": str(path), "line": line}))


def _written_in(path: Path) -> str:
    try:
        written = path.read_bytes()
    except OSError as failed:
        refusal = next(row.refusal for row in _READ_FAILURES if isinstance(failed, row.raised))
        why = failed.strerror or str(failed)
        raise TermsUnreadable(Message(refusal, {"path": str(path), "why": why})) from None
    try:
        return written.decode("utf-8-sig")
    except UnicodeDecodeError as failed:
        raise _refused(
            "terms_not_utf8", path, written.count(b"\n", 0, failed.start) + 1
        ) from None


def _blocks_in(written: str) -> list[_Block]:
    blocks: list[_Block] = []
    lines: list[str] = []
    for number, line in enumerate([*written.splitlines(), ""], start=1):
        if line.strip():
            lines.append(line.strip())
            continue
        if lines:
            blocks.append(_Block(number - len(lines), tuple(lines)))
        lines = []
    return blocks


def _title_in(block: _Block, path: Path) -> tuple[str, str | None]:
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
    drawn = _refusal_in(text, _BLOCK_STARTS)
    if drawn in (_QUOTE.refusal, _RULE.refusal):
        raise _refused(drawn, path, line)
    if drawn is not None:
        raise _refused("terms_date_formatted", path, line)
    _refuse_formatted(text, path=path, line=line, own="terms_date_formatted")


def _refuse_formatted(text: str, *, path: Path, line: int, own: str) -> None:
    drawn = _refusal_in(text, _IN_LINES)
    if drawn == "terms_emphasis":
        raise _refused(own, path, line)
    if drawn is not None:
        raise _refused(drawn, path, line)


def _paragraph_in(block: _Block, path: Path) -> TermsParagraph:
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
    drawn = _refusal_in(text, unshowable)
    if drawn is not None:
        raise _refused(drawn, path, line)


def _refusal_in(text: str, unshowable: tuple[_Unshowable, ...]) -> str | None:
    return next((row.refusal for row in unshowable if row.drawn.search(text)), None)


def _refuse_a_lead_said_twice(
    paragraphs: list[TermsParagraph], blocks: list[_Block], path: Path
) -> None:
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
    shown_to = 0
    for address in _EMAIL.finditer(text):
        if address.start() > shown_to:
            yield {"kind": "words", "text": text[shown_to : address.start()]}
        yield {"kind": "email", "address": address[0]}
        shown_to = address.end()
    if shown_to < len(text):
        yield {"kind": "words", "text": text[shown_to:]}
