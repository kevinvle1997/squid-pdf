"""The file's own copy of a font: whether we can use it, and how we'd write with it.

Read through the driver, so it's the same whatever library is underneath.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from squidpdf.core.coverage import Coverage
from squidpdf.core.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.message import Message
from squidpdf.core.types import CodedFont, FontCode, FontKind, PageFont

__all__ = [
    "EmbeddedFont",
    "FontUnusable",
    "open_embedded",
]

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES: dict[FontKind, int] = {"truetype": 1, "type0": 2}


@dataclass(frozen=True, slots=True)
class EmbeddedFont:
    """A font stored in the file, and what it really draws."""

    program: FontProgram  # the driver's open copy, to measure with
    file: bytes  # the font file itself, to add to a page for a redraw
    coverage: Coverage  # which letters draw a shape
    coded: CodedFont | None  # set when we write it by code, not by letter


class FontUnusable(Exception):
    """Why the file's own copy of a font can't be used, so a similar font draws instead."""

    def __init__(self, reason: Message) -> None:
        """`reason` names a sentence in `core.words`, for the edge to put into words."""
        super().__init__(reason)
        self.reason = reason


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
        return open_font_file(driver, page_font, font_file)
    except DriverError as problem:  # the library can't read the font out, open it or load it
        raise FontUnusable(problem.reason) from problem


def open_font_file(driver: PdfDriver, page_font: PageFont, font_file: bytes) -> EmbeddedFont:
    """The font opened, by letter when it looks letters up itself, else by code."""
    program = driver.open_font(font_file)
    listed_letters = program.listed_letters()
    coverage = Coverage(font_file, listed_letters)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return EmbeddedFont(program, font_file, coverage, None)

    # Written by code, as its letter list says.
    try:
        coded, coded_coverage = read_by_code(driver, page_font, font_file)
    except FontUnusable:
        # Unreadable either way: keep it only if the library says it has letters.
        if listed_letters:
            return EmbeddedFont(program, font_file, coverage, None)
        raise
    return EmbeddedFont(program, font_file, coded_coverage, coded)


def read_by_code(
    driver: PdfDriver, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage]:
    """The font's codes and which letters they really draw. Raises FontUnusable if we can't."""
    # Only a TrueType font the page uses itself, not one inside a form (a reusable drawing).
    writable = page_font.file_type == "truetype" and not page_font.in_form
    if not writable:
        raise FontUnusable(Message("font_cant_write"))
    coded = read_coded_font(driver, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = Coverage(font_file, glyph_ids=glyph_ids)
    if not coverage.usable:
        raise FontUnusable(Message("font_unreadable"))
    return coded, coverage


def read_coded_font(driver: PdfDriver, font: PageFont) -> CodedFont:
    """Which code draws each letter, from the font's letter list (ToUnicode).

    Raises FontUnusable without one: guessing would draw the wrong letters.
    """
    code_bytes = bytes_per_code(font)
    if code_bytes is None:
        raise FontUnusable(Message("font_cant_write"))
    font_codes = driver.font_codes(font.xref, code_bytes)
    if font_codes is None:
        raise FontUnusable(Message("font_no_letter_list"))

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        # Only codes that draw a shape, for a letter someone could type.
        typeable = code.glyph != 0 and not is_control(code.letter)
        if typeable:
            letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        raise FontUnusable(Message("font_no_letter_list"))
    return CodedFont(font.resource, font.xref, code_bytes, letters)


def bytes_per_code(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    return _CODE_BYTES.get(font.kind)


def is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")
