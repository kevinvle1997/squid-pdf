from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.coverage import Coverage, coverage_of
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.types import EM, CodedFont, FontCode, FontKind, PageFont

_CODE_BYTES: dict[FontKind, int] = {"truetype": 1, "type0": 2, "type3": 1}


@dataclass(frozen=True, slots=True, eq=False)
class EmbeddedFont:
    """`eq=False`: comparing two would compare whole font files."""

    program: FontProgram
    file: bytes = field(repr=False)
    coded: CodedFont | None


class FontUnusable(Exception):
    def __init__(self, reason: Message) -> None:
        super().__init__(reason)
        self.reason = reason


def remembered[K, V](
    memo: dict[K, V | FontUnusable], key: K, make: Callable[[], V]
) -> V | FontUnusable:
    """A failure is kept as its reason alone: its traceback would keep the font's bytes."""
    if key not in memo:
        try:
            memo[key] = make()
        except FontUnusable as problem:
            memo[key] = FontUnusable(problem.reason)
    return memo[key]


def recalled[K, V](memo: dict[K, V | FontUnusable], key: K, make: Callable[[], V]) -> V:
    found = remembered(memo, key, make)
    if isinstance(found, FontUnusable):
        raise FontUnusable(found.reason)
    return found


def made_once[K, V](memo: dict[K, V], key: K, make: Callable[[], V]) -> V:
    if key not in memo:
        memo[key] = make()
    return memo[key]


def open_embedded(driver: PdfDriver, page_font: PageFont) -> tuple[EmbeddedFont, Coverage]:
    """Without a letter lookup it still counts if its letter list (ToUnicode) maps each code."""
    if page_font.kind == "type3":
        return _open_drawn(driver, page_font)
    if not page_font.is_embedded:
        raise FontUnusable(Message("font_not_in_file"))
    try:
        font_file = driver.font_bytes(page_font.font_object)
        return _open_font_file(driver, page_font, font_file)
    except DriverError as problem:
        raise FontUnusable(problem.reason) from problem


def _open_drawn(driver: PdfDriver, page_font: PageFont) -> tuple[EmbeddedFont, Coverage]:
    if page_font.in_form:
        raise FontUnusable(Message("font_cant_write"))
    coded = _read_coded_font(driver, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = coverage_of(b"", (ord(letter) for letter in coded.letters), glyph_ids)
    return EmbeddedFont(_DrawnLetters(coded), b"", coded), coverage


@dataclass(frozen=True, slots=True)
class _DrawnLetters:
    coded: CodedFont

    def listed_letters(self) -> list[int]:
        return [ord(letter) for letter in self.coded.letters]

    def advance(self, ch: str) -> float:
        # a space it lacks is still measured, for the page's usual gap
        code = self.coded.letters.get(ch)
        return 0.0 if code is None else code.width / EM

    def maps(self, ch: str) -> bool:
        return ch in self.coded.letters

    def width(self, text: str, size: float) -> float:
        return sum(self.advance(ch) for ch in text) * size


def _open_font_file(
    driver: PdfDriver, page_font: PageFont, font_file: bytes
) -> tuple[EmbeddedFont, Coverage]:
    program = driver.open_font(font_file)
    listed_letters = program.listed_letters()
    coverage = coverage_of(font_file, listed_letters)

    if coverage.usable:
        return EmbeddedFont(program, font_file, None), coverage

    try:
        coded, coded_coverage = _read_by_code(driver, page_font, font_file)
    except FontUnusable:
        if listed_letters:
            return EmbeddedFont(program, font_file, None), coverage
        raise
    return EmbeddedFont(program, font_file, coded), coded_coverage


def _read_by_code(
    driver: PdfDriver, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage]:
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
    """Raises FontUnusable without one: guessing would draw the wrong letters."""
    code_bytes = _bytes_per_code(font)
    if code_bytes is None:
        raise FontUnusable(Message("font_cant_write"))
    font_codes = driver.font_codes(font.font_object, code_bytes)
    if font_codes is None:
        raise FontUnusable(Message("font_no_letter_list"))

    typeable = tuple(
        code for code in font_codes if code.glyph != 0 and not _is_control(code.letter)
    )
    letters: dict[str, FontCode] = {}
    for code in typeable:
        letters.setdefault(code.letter, code)
    if not letters:
        raise FontUnusable(Message("font_no_letter_list"))
    return CodedFont(code_bytes, letters, typeable)


def _bytes_per_code(font: PageFont) -> int | None:
    if font.kind == "type0" and not font.codes_are_cids:
        return None
    return _CODE_BYTES.get(font.kind)


def _is_control(ch: str) -> bool:
    return unicodedata.category(ch).startswith("C")
