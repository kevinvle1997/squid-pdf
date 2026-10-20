"""The file's own copy of a font: whether we can use it, and how we'd write with it.

Read through the driver, so it's the same whatever library is underneath.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.coverage import Coverage, coverage_of
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.types import EM, CodedFont, FontCode, FontKind, PageFont

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES: dict[FontKind, int] = {"truetype": 1, "type0": 2, "type3": 1}


@dataclass(frozen=True, slots=True, eq=False)
class EmbeddedFont:
    """A font stored in the file, or lent from outside it, opened to measure and write with.

    Made of parts, a font program and its file, so `eq=False`: comparing
    two would compare whole font files.
    """

    program: FontProgram  # what measures it: the driver's open copy, or a Type3's width list
    # The font file itself, to add to a page for a redraw; empty for a Type3's drawings.
    file: bytes = field(repr=False)
    coded: CodedFont | None  # set when we write it by code, not by letter


class FontUnusable(Exception):
    """Why a copy of a font can't be used, for the edge to say.

    The file's own copy, so a similar font draws instead; or a copy lent from
    outside the file, as Google's is, so it lends no letters.
    """

    def __init__(self, reason: Message) -> None:
        """`reason` names a sentence in `core.app.words`, for the edge to put into words."""
        super().__init__(reason)
        self.reason = reason


def remembered[K, V](
    memo: dict[K, V | FontUnusable], key: K, make: Callable[[], V]
) -> V | FontUnusable:
    """What `make()` made for `key` the first time, or why it couldn't: made once per key.

    A second ask gets the same answer, so a reason is said the same way each time.
    A failure is kept as its reason alone: the raised one's traceback and cause would
    keep what `make` held, the font's bytes, for as long as the memo lives.
    """
    if key not in memo:
        try:
            memo[key] = make()
        except FontUnusable as problem:  # `make` couldn't, and says why
            memo[key] = FontUnusable(problem.reason)
    return memo[key]


def recalled[K, V](memo: dict[K, V | FontUnusable], key: K, make: Callable[[], V]) -> V:
    """What `remembered` gives, for a caller that raises the reason it couldn't be made."""
    found = remembered(memo, key, make)
    # Couldn't be made: said as it was the first time it was asked for.
    if isinstance(found, FontUnusable):
        raise FontUnusable(found.reason)
    return found


def made_once[K, V](memo: dict[K, V], key: K, make: Callable[[], V]) -> V:
    """What `make()` made for `key` the first time: made once per key, then kept."""
    if key not in memo:
        memo[key] = make()
    return memo[key]


def open_embedded(driver: PdfDriver, page_font: PageFont) -> tuple[EmbeddedFont, Coverage]:
    """The file's copy of a font, opened, and what it draws. Raises FontUnusable, saying why.

    A font with no letter lookup of its own (only a symbol table, say) still
    counts if its letter list (ToUnicode) says which code draws each letter;
    `draw` then writes those codes.
    """
    # Each letter a drawing in the file: written by code, in those drawings.
    if page_font.kind == "type3":
        return _open_drawn(driver, page_font)
    # Not in the file at all: only named.
    if not page_font.is_embedded:
        raise FontUnusable(Message("font_not_in_file"))
    try:
        font_file = driver.font_bytes(page_font.xref)
        return _open_font_file(driver, page_font, font_file)
    except DriverError as problem:  # the library can't read the font out, open it or load it
        raise FontUnusable(problem.reason) from problem


def _open_drawn(driver: PdfDriver, page_font: PageFont) -> tuple[EmbeddedFont, Coverage]:
    """A Type3 font, written by code and measured by its width list: no font file to open."""
    # Only a font the page uses itself, as for a TrueType written by code.
    if page_font.in_form:
        raise FontUnusable(Message("font_cant_write"))
    coded = _read_coded_font(driver, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    # No file to read: its letter list says what it draws, a space only if it has one.
    coverage = coverage_of(b"", (ord(letter) for letter in coded.letters), glyph_ids)
    return EmbeddedFont(_DrawnLetters(coded), b"", coded), coverage


@dataclass(frozen=True, slots=True)
class _DrawnLetters:
    """A Type3 font's letters, measured by its width list, as the page places them."""

    coded: CodedFont

    def listed_letters(self) -> list[int]:
        """Every letter it has a drawing for."""
        return [ord(letter) for letter in self.coded.letters]

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems: none for a letter it has no drawing for."""
        # .get: a space it lacks is still measured, for the page's usual gap.
        code = self.coded.letters.get(ch)
        return 0.0 if code is None else code.width / EM

    def maps(self, ch: str) -> bool:
        """Whether it has a drawing for `ch`."""
        return ch in self.coded.letters

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points."""
        return sum(self.advance(ch) for ch in text) * size


def _open_font_file(
    driver: PdfDriver, page_font: PageFont, font_file: bytes
) -> tuple[EmbeddedFont, Coverage]:
    """The font opened, by letter when it looks letters up itself, else by code."""
    program = driver.open_font(font_file)
    listed_letters = program.listed_letters()
    coverage = coverage_of(font_file, listed_letters)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return EmbeddedFont(program, font_file, None), coverage

    # Written by code, as its letter list says.
    try:
        coded, coded_coverage = _read_by_code(driver, page_font, font_file)
    except FontUnusable:
        # Unreadable either way: keep it only if the library says it has letters.
        if listed_letters:
            return EmbeddedFont(program, font_file, None), coverage
        raise
    return EmbeddedFont(program, font_file, coded), coded_coverage


def _read_by_code(
    driver: PdfDriver, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage]:
    """The font's codes and which letters they really draw. Raises FontUnusable if we can't."""
    # Only a TrueType font the page uses itself, not one inside a form (a reusable drawing).
    writable = page_font.file_type == "truetype" and not page_font.in_form
    if not writable:
        raise FontUnusable(Message("font_cant_write"))
    coded = _read_coded_font(driver, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = coverage_of(font_file, glyph_ids=glyph_ids)
    if not coverage.usable:
        raise FontUnusable(Message("font_unreadable"))
    return coded, coverage


def _read_coded_font(driver: PdfDriver, font: PageFont) -> CodedFont:
    """Which code draws each letter, from the font's letter list (ToUnicode).

    Raises FontUnusable without one: guessing would draw the wrong letters.
    """
    code_bytes = _bytes_per_code(font)
    if code_bytes is None:
        raise FontUnusable(Message("font_cant_write"))
    font_codes = driver.font_codes(font.xref, code_bytes)
    if font_codes is None:
        raise FontUnusable(Message("font_no_letter_list"))

    # Only codes that draw a shape, for a letter someone could type; lowest code first.
    typeable = tuple(
        code for code in font_codes if code.glyph != 0 and not _is_control(code.letter)
    )
    letters: dict[str, FontCode] = {}
    for code in typeable:
        letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        raise FontUnusable(Message("font_no_letter_list"))
    return CodedFont(code_bytes, letters, typeable)


def _bytes_per_code(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    # .get: a kind we can't write by code, as Type1, has none.
    return _CODE_BYTES.get(font.kind)


def _is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")
