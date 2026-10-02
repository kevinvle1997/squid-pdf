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
from squidpdf.core.types import CodedFont, FontCode, FontKind, PageFont

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES: dict[FontKind, int] = {"truetype": 1, "type0": 2}


@dataclass(frozen=True, slots=True, eq=False)
class EmbeddedFont:
    """A font stored in the file, and what it really draws.

    Made of parts, a font program and its coverage, so `eq=False`: comparing
    two would compare whole font files.
    """

    program: FontProgram  # the driver's open copy, to measure with
    file: bytes = field(repr=False)  # the font file itself, to add to a page for a redraw
    coverage: Coverage  # which letters draw a shape
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
    """
    if key not in memo:
        try:
            memo[key] = make()
        except FontUnusable as problem:  # `make` couldn't, and says why
            memo[key] = problem
    return memo[key]


def made_once[K, V](memo: dict[K, V], key: K, make: Callable[[], V]) -> V:
    """What `make()` made for `key` the first time: made once per key, then kept."""
    if key not in memo:
        memo[key] = make()
    return memo[key]


def open_embedded(driver: PdfDriver, page_font: PageFont) -> EmbeddedFont:
    """Open the file's copy of a font. Raises FontUnusable, saying why, if we can't use it.

    A font with no letter lookup of its own (only a symbol table, say) still
    counts if its letter list (ToUnicode) says which code draws each letter;
    `draw` then writes those codes.
    """
    # Not in the file at all: only named.
    if not page_font.is_embedded:
        raise FontUnusable(Message("font_not_in_file"))
    try:
        font_file = driver.font_bytes(page_font.xref)
        return _open_font_file(driver, page_font, font_file)
    except DriverError as problem:  # the library can't read the font out, open it or load it
        raise FontUnusable(problem.reason) from problem


def _open_font_file(driver: PdfDriver, page_font: PageFont, font_file: bytes) -> EmbeddedFont:
    """The font opened, by letter when it looks letters up itself, else by code."""
    program = driver.open_font(font_file)
    listed_letters = program.listed_letters()
    coverage = coverage_of(font_file, listed_letters)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return EmbeddedFont(program, font_file, coverage, None)

    # Written by code, as its letter list says.
    try:
        coded, coded_coverage = _read_by_code(driver, page_font, font_file)
    except FontUnusable:
        # Unreadable either way: keep it only if the library says it has letters.
        if listed_letters:
            return EmbeddedFont(program, font_file, coverage, None)
        raise
    return EmbeddedFont(program, font_file, coded_coverage, coded)


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

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        # Only codes that draw a shape, for a letter someone could type.
        typeable = code.glyph != 0 and not _is_control(code.letter)
        if typeable:
            letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        raise FontUnusable(Message("font_no_letter_list"))
    return CodedFont(code_bytes, letters)


def _bytes_per_code(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    return _CODE_BYTES.get(font.kind)


def _is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")
