"""The file's own copy of a font: whether we can use it, and how we'd write with it.

Read through the backend, so it's the same whatever library is underneath.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from squidpdf.core import words
from squidpdf.core.backend import Backend, FontProgram
from squidpdf.core.coverage import Coverage
from squidpdf.core.types import CodedFont, FontCode, PageFont

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES = {"TrueType": 1, "Type0": 2}


@dataclass(frozen=True, slots=True)
class EmbeddedFont:
    """A font stored in the file, and what it really draws."""

    program: FontProgram  # the backend's open copy, to measure with
    file: bytes  # the font file itself, to add to a page for a redraw
    coverage: Coverage  # which letters draw a shape
    coded: CodedFont | None  # set when we write it by code, not by letter


class FontUnusable(Exception):
    """Why the file's own copy of a font can't be used, so a similar font draws instead."""

    def __init__(self, reason: str) -> None:
        """`reason` is a sentence from `core.words`, shown to the user as it is."""
        super().__init__(reason)
        self.reason = reason


def open_embedded(backend: Backend, page_font: PageFont) -> EmbeddedFont:
    """Open the file's copy of a font. Raises FontUnusable, saying why, if we can't use it.

    A font with no letter lookup of its own (only a symbol table, say) still
    counts if its letter list (ToUnicode) says which code draws each letter;
    `draw` then writes those codes.
    """
    # Not in the file at all: only named.
    if not page_font.is_embedded:
        raise FontUnusable(words.FONT_NOT_IN_FILE)
    font_file = backend.font_bytes(page_font.xref)
    # Stored, but the library can't read it out.
    if not font_file:
        raise FontUnusable(words.FONT_UNREADABLE)
    try:
        return _open(backend, page_font, font_file)
    except ValueError as exc:  # the library can't read the font
        raise FontUnusable(words.FONT_UNREADABLE) from exc


def _open(backend: Backend, page_font: PageFont, font_file: bytes) -> EmbeddedFont:
    """The font opened, by letter when it looks letters up itself, else by code."""
    program = backend.open_font(font_file)
    claimed = program.claimed()
    coverage = Coverage(font_file, claimed)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return EmbeddedFont(program, font_file, coverage, None)

    # Written by code, as its letter list says.
    try:
        coded, coded_coverage = _read_by_code(backend, page_font, font_file)
    except FontUnusable:
        # Unreadable either way: keep it only if the library says it has letters.
        if claimed:
            return EmbeddedFont(program, font_file, coverage, None)
        raise
    return EmbeddedFont(program, font_file, coded_coverage, coded)


def _read_by_code(
    backend: Backend, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage]:
    """The font's codes and which letters they really draw. Raises FontUnusable if we can't."""
    # Only a TrueType font the page uses itself, not one inside a form (a reusable drawing).
    writable = page_font.file_type == "ttf" and not page_font.in_form
    if not writable:
        raise FontUnusable(words.FONT_CANT_WRITE)
    coded = _read_coded_font(backend, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = Coverage(font_file, glyph_ids=glyph_ids)
    if not coverage.usable:
        raise FontUnusable(words.FONT_UNREADABLE)
    return coded, coverage


def _read_coded_font(backend: Backend, font: PageFont) -> CodedFont:
    """Which code draws each letter, from the font's letter list (ToUnicode).

    Raises FontUnusable without one: guessing would draw the wrong letters.
    """
    code_bytes = _code_bytes(font)
    if code_bytes is None:
        raise FontUnusable(words.FONT_CANT_WRITE)
    font_codes = backend.font_codes(font.xref, code_bytes)
    if font_codes is None:
        raise FontUnusable(words.FONT_NO_LETTER_LIST)

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        # Only codes that draw a shape, for a letter someone could type.
        typeable = code.glyph != 0 and not _is_control(code.letter)
        if typeable:
            letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        raise FontUnusable(words.FONT_NO_LETTER_LIST)
    return CodedFont(font.resource, font.xref, code_bytes, letters)


def _code_bytes(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "Type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    return _CODE_BYTES.get(font.kind)


def _is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")
