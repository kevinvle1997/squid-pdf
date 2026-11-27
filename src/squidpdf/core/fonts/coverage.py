"""A subset font still lists glyphs it emptied: only a glyph's own outline says it draws."""

from __future__ import annotations

import io
import struct
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fontTools.agl import toUnicode
from fontTools.cffLib import CFFFontSet
from fontTools.pens.basePen import DecomposingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph, table__g_l_y_f

from squidpdf.core.constants import GLYPH_LIST_RANGES
from squidpdf.core.types import Codepoint, GlyphId, GlyphName

# Text extraction adds a plain space where a font drew none; any other space needs mapping.
_ALWAYS_DRAWABLE = frozenset({" "})


_BARE_CFF_SIGNATURE = b"\x01\x00"

_CFF_TABLES = ("CFF ", "CFF2")

_OUTLINE_COUNT = struct.Struct(">h")


@dataclass(frozen=True, slots=True, eq=False)
class Coverage:
    """Cached per letter: it runs on every keystroke."""

    usable: bool
    listed_letters: frozenset[int] = field(repr=False)
    always_drawable: frozenset[str]
    glyph_names: dict[Codepoint, GlyphName] = field(default_factory=dict, repr=False)
    glyphs: Mapping[GlyphName, Any] = field(default_factory=dict, repr=False)
    truetype: table__g_l_y_f | None = None
    cache: dict[str, bool] = field(default_factory=dict, repr=False)

    def covers(self, ch: str) -> bool:
        if ch in self.always_drawable:
            return True
        if not self.usable:
            return ord(ch) in self.listed_letters
        if ch.isspace():
            return Codepoint(ord(ch)) in self.glyph_names
        draws = self.cache.get(ch)
        if draws is None:
            draws = self.cache[ch] = self._draws(ch)
        return draws

    def _draws(self, ch: str) -> bool:
        """A trimmed font can keep a built letter (Á) while emptying its parts."""
        name = self.glyph_names.get(Codepoint(ord(ch)))
        if name is None:
            return False
        try:
            return self._has_outline(name)
        except Exception:  # noqa: BLE001 (a glyph that won't read, or is built from itself, is missing)
            return False

    def _has_outline(self, name: GlyphName) -> bool:
        if self.truetype is not None:
            return _truetype_has_outline(self.truetype, name)
        return _cff_has_outline(self.glyphs, name)

    def drawable(self) -> list[str]:
        codes = self.glyph_names if self.usable else self.listed_letters
        chars = (chr(codepoint) for codepoint in codes)
        return sorted(self.always_drawable.union(ch for ch in chars if self.covers(ch)))

    def previewed_letters(self) -> tuple[str, ...]:
        previewed = (chr(codepoint) for block in GLYPH_LIST_RANGES for codepoint in block)
        return tuple(ch for ch in previewed if self.covers(ch))

    def missing(self, text: str) -> list[str]:
        unique = dict.fromkeys(text)
        return [ch for ch in unique if not self.covers(ch)]


def coverage_of(
    buffer: bytes,
    listed_letters: Iterable[int] = (),
    glyph_ids: Mapping[str, GlyphId] | None = None,
) -> Coverage:
    listed = frozenset(filter(_is_letter_code, listed_letters))
    # With glyph_ids the font is written by code, and a space with no code can't be.
    always_drawable = _ALWAYS_DRAWABLE if glyph_ids is None else frozenset[str]()
    unparsed = Coverage(usable=False, listed_letters=listed, always_drawable=always_drawable)
    if not buffer:
        return unparsed
    try:
        if buffer[:2] == _BARE_CFF_SIGNATURE:
            return _bare_cff_coverage(buffer, listed=listed, always_drawable=always_drawable)
        return _sfnt_coverage(buffer, glyph_ids, listed=listed, always_drawable=always_drawable)
    except Exception:  # noqa: BLE001 (a font we cannot parse is not a crash)
        return unparsed


def _sfnt_coverage(
    buffer: bytes,
    glyph_ids: Mapping[str, GlyphId] | None,
    *,
    listed: frozenset[int],
    always_drawable: frozenset[str],
) -> Coverage:
    font = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
    glyph_names = _glyph_name_for_each_letter(font, glyph_ids)
    glyphs = font.getGlyphSet()
    truetype_only = "glyf" in font and not any(table in font for table in _CFF_TABLES)
    return Coverage(
        usable=True,
        listed_letters=listed,
        always_drawable=always_drawable,
        glyph_names=glyph_names,
        glyphs=glyphs,
        truetype=font["glyf"] if truetype_only else None,
    )


def _bare_cff_coverage(
    buffer: bytes, *, listed: frozenset[int], always_drawable: frozenset[str]
) -> Coverage:
    """A CID-keyed CFF names glyphs by CID, which the bytes can't map to Unicode."""
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(buffer), None)
    font = cff[cff.fontNames[0]]
    if getattr(font, "ROS", None) is not None:
        raise ValueError("CID-keyed CFF: no Unicode mapping from bytes alone")
    glyph_names: dict[Codepoint, GlyphName] = {}
    for name in font.getGlyphOrder():
        letter = _letter_named(name)
        if letter is not None:
            glyph_names.setdefault(Codepoint(ord(letter)), name)
    return Coverage(
        usable=True,
        listed_letters=listed,
        always_drawable=always_drawable,
        glyph_names=glyph_names,
        glyphs=font.CharStrings,
    )


def _truetype_has_outline(truetype: table__g_l_y_f, name: GlyphName) -> bool:
    glyph = truetype.glyphs[name]
    if glyph.isComposite():
        parts = [part.glyphName for part in truetype[name].components]
        return any(_truetype_has_outline(truetype, part) for part in parts if part in truetype)
    return _outline_count(glyph) > 0


def _outline_count(glyph: Glyph) -> int:
    if not hasattr(glyph, "data"):
        return glyph.numberOfContours
    return _OUTLINE_COUNT.unpack_from(glyph.data)[0]


def _cff_has_outline(glyphs: Mapping[GlyphName, Any], name: GlyphName) -> bool:
    try:
        glyphs[name].draw(_InkPen(glyphs))
    except _Inked:
        return True
    return False


class _Inked(Exception): ...


class _InkPen(DecomposingPen):
    """fontTools adds a move a glyph's code leaves out: every outline starts with one."""

    def moveTo(self, pt: tuple[float, float]) -> None:
        raise _Inked


def _glyph_name_for_each_letter(
    font: TTFont, glyph_ids: Mapping[str, GlyphId] | None
) -> dict[Codepoint, GlyphName]:
    """With neither it raises, so the font is unusable rather than missing every letter."""
    if glyph_ids is not None:
        order = font.getGlyphOrder()
        return {
            Codepoint(ord(letter)): order[glyph_id]
            for letter, glyph_id in glyph_ids.items()
            if glyph_id < len(order)
        }
    cmap = font.getBestCmap()
    if cmap is None:
        raise ValueError("no Unicode cmap: characters can't be matched to glyphs")
    return {
        Codepoint(codepoint): name
        for codepoint, name in cmap.items()
        if _is_letter_code(codepoint)
    }


def _is_letter_code(codepoint: int) -> bool:
    return 0 <= codepoint <= sys.maxunicode


def _letter_named(glyph_name: GlyphName) -> str | None:
    """A variant ("a.alt", "T.sc") is another shape for the letter, not its own."""
    if "." in glyph_name:
        return None
    letter = toUnicode(glyph_name)
    return letter if len(letter) == 1 else None
