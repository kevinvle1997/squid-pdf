"""What a font can actually draw, as opposed to what it claims.

A subsetted font still lists glyphs it emptied the outlines of: has_glyph and
valid_codepoints report the claim, not reality. The honest test reads the
glyph's own outline, and those of the parts it's built from.
"""

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

from squidpdf.core.types import Codepoint, GlyphId, GlyphName

__all__ = [
    "Coverage",
    "coverage_of",
]

# Counted as drawable whether the font maps it or not: the plain space is one text
# extraction adds between words a font never drew a space for. Any other space
# (no-break, figure, thin...) draws nothing yet still needs the font to map it:
# unmapped, it's drawn as .notdef. Line breaks and tabs aren't text on a line.
_ALWAYS_DRAWABLE = frozenset({" "})


# A bare CFF font program starts with this header (major.minor version 1.0);
# anything else handed to Coverage is assumed to be a TrueType/OpenType wrapper.
_BARE_CFF_SIGNATURE = b"\x01\x00"

# The tables CFF outlines come in, in an OpenType font: version 1 and 2.
_CFF_TABLES = ("CFF ", "CFF2")

# A TrueType glyph starts with its outline count, a signed 16-bit big-endian number.
_OUTLINE_COUNT = struct.Struct(">h")


@dataclass(frozen=True, slots=True, eq=False)
class Coverage:
    """Which characters an embedded font program can really render. Made by `coverage_of`.

    Built from the raw bytes as extracted from the PDF, which may be a bare CFF
    (Adobe's compact outline format), a TrueType, or an OpenType wrapper. Bytes
    it can't read (Type1, a font whose letter table has no Unicode) fall back to
    `listed_letters`, the font engine's own list: the best word left. A TrueType with no
    Unicode letter table can be given `glyph_ids` (letter -> glyph id) instead;
    then only those letters count.
    Results are cached per character because the check runs on every keystroke.
    """

    usable: bool  # its bytes parsed: else only `listed_letters` says what it draws
    # What the font engine lists, the word left for bytes that won't parse.
    listed_letters: frozenset[int] = field(repr=False)
    # Drawable whether mapped or not: the plain space, unless the font is written by code.
    always_drawable: frozenset[str]
    # Which shape draws each letter: empty for bytes that won't parse.
    glyph_names: dict[Codepoint, GlyphName] = field(default_factory=dict, repr=False)
    glyphs: Mapping[GlyphName, Any] = field(default_factory=dict, repr=False)  # to draw from
    truetype: table__g_l_y_f | None = None  # TrueType glyphs, read without drawing
    # Each character's answer, checked every keystroke.
    cache: dict[str, bool] = field(default_factory=dict, repr=False)

    def covers(self, ch: str) -> bool:
        """True when this font really puts ink on the page for `ch`, or places its space."""
        if ch in self.always_drawable:
            return True
        if not self.usable:
            return ord(ch) in self.listed_letters
        # A space draws no ink, so being mapped is all it takes.
        if ch.isspace():
            return Codepoint(ord(ch)) in self.glyph_names
        draws = self.cache.get(ch)
        if draws is None:
            draws = self.cache[ch] = self._draws(ch)
        return draws

    def _draws(self, ch: str) -> bool:
        """Whether the glyph for `ch` has any outline, so puts ink on the page.

        A letter built from others (Á from A and an accent) counts through to
        their outlines: a trimmed font can keep it while emptying its parts.
        """
        name = self.glyph_names.get(Codepoint(ord(ch)))  # None: the font doesn't map it
        if name is None:
            return False
        try:
            return self._has_outline(name)
        except Exception:  # noqa: BLE001 (a glyph that won't read, or is built from itself, is missing)
            return False

    def _has_outline(self, name: GlyphName) -> bool:
        """Whether the glyph `name` has an outline: read for TrueType, drawn for CFF."""
        if self.truetype is not None:
            return truetype_has_outline(self.truetype, name)
        return cff_has_outline(self.glyphs, name)

    def drawable(self) -> list[str]:
        """Every character `covers` says draws, spaces included, in code point order."""
        codes = self.glyph_names if self.usable else self.listed_letters
        chars = (chr(codepoint) for codepoint in codes)
        return sorted(self.always_drawable.union(ch for ch in chars if self.covers(ch)))

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that this font cannot draw, in order, deduped."""
        unique = dict.fromkeys(text)  # drops repeats, keeps first-seen order
        return [ch for ch in unique if not self.covers(ch)]


def coverage_of(
    buffer: bytes,
    listed_letters: Iterable[int] = (),
    glyph_ids: Mapping[str, GlyphId] | None = None,
) -> Coverage:
    """Parse a font's raw bytes; `listed_letters` is what it draws if they won't parse."""
    listed = frozenset(filter(is_letter_code, listed_letters))
    # With glyph_ids the font is written by code, and a space with no code can't be.
    always_drawable = _ALWAYS_DRAWABLE if glyph_ids is None else frozenset[str]()
    unparsed = Coverage(usable=False, listed_letters=listed, always_drawable=always_drawable)
    # No bytes: nothing to parse.
    if not buffer:
        return unparsed
    try:
        # A bare CFF, as CIDFontType0 subsets are embedded.
        if buffer[:2] == _BARE_CFF_SIGNATURE:
            return bare_cff_coverage(buffer, listed=listed, always_drawable=always_drawable)
        # A TrueType or OpenType file.
        return sfnt_coverage(buffer, glyph_ids, listed=listed, always_drawable=always_drawable)
    except Exception:  # noqa: BLE001 (a font we cannot parse is not a crash)
        return unparsed


def sfnt_coverage(
    buffer: bytes,
    glyph_ids: Mapping[str, GlyphId] | None,
    *,
    listed: frozenset[int],
    always_drawable: frozenset[str],
) -> Coverage:
    """TrueType or OpenType, mapped by its letter table (cmap) or by `glyph_ids`."""
    font = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
    glyph_names = glyph_name_for_each_letter(font, glyph_ids)
    glyphs = font.getGlyphSet()  # raises for a font missing a table drawing needs
    # TrueType outlines are read, not drawn; a font with CFF outlines too draws those.
    truetype_only = "glyf" in font and not any(table in font for table in _CFF_TABLES)
    return Coverage(
        usable=True,
        listed_letters=listed,
        always_drawable=always_drawable,
        glyph_names=glyph_names,
        glyphs=glyphs,
        truetype=font["glyf"] if truetype_only else None,
    )


def bare_cff_coverage(
    buffer: bytes, *, listed: frozenset[int], always_drawable: frozenset[str]
) -> Coverage:
    """A bare CFF, as CIDFontType0 subsets are embedded.

    There is no cmap here, so each glyph's name says its letter: Adobe's
    names ("eacute", "fi" for the ligature ﬁ) or "uni00E9". That only
    works for name-keyed CFFs; a CID-keyed one carries CID glyph names
    instead (`cid00034`, not `eacute`), which cannot be mapped back to
    Unicode from the font bytes alone. Raised so the font is marked
    unusable rather than silently reporting every character as missing.
    """
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(buffer), None)
    font = cff[cff.fontNames[0]]
    if getattr(font, "ROS", None) is not None:
        raise ValueError("CID-keyed CFF: no Unicode mapping from bytes alone")
    glyph_names: dict[Codepoint, GlyphName] = {}
    # The glyph order's: the first shape named for a letter draws it.
    for name in font.getGlyphOrder():
        letter = letter_named(name)
        if letter is not None:
            glyph_names.setdefault(Codepoint(ord(letter)), name)
    return Coverage(
        usable=True,
        listed_letters=listed,
        always_drawable=always_drawable,
        glyph_names=glyph_names,
        glyphs=font.CharStrings,
    )


def truetype_has_outline(truetype: table__g_l_y_f, name: GlyphName) -> bool:
    """Whether a TrueType glyph has outlines of its own, or is built from parts that do."""
    glyph = truetype.glyphs[name]
    # Built from other glyphs (Á from A and an accent): it draws if one of its parts does.
    if glyph.isComposite():
        # A part the font lacks draws nothing, as a drawing skips it.
        parts = [part.glyphName for part in truetype[name].components]
        return any(truetype_has_outline(truetype, part) for part in parts if part in truetype)
    # Its own outlines: an emptied glyph, as a trimmed font leaves it, has none.
    return outline_count(glyph) > 0


def outline_count(glyph: Glyph) -> int:
    """How many outlines a glyph that isn't built from others has.

    Read from the glyph's first two bytes, so its points are never unpacked.
    """
    # An empty glyph has no bytes, and one fontTools has unpacked keeps its count.
    if not hasattr(glyph, "data"):
        return glyph.numberOfContours
    return _OUTLINE_COUNT.unpack_from(glyph.data)[0]


def cff_has_outline(glyphs: Mapping[GlyphName, Any], name: GlyphName) -> bool:
    """Whether a CFF glyph draws anything, drawn only as far as its first outline's start."""
    try:
        glyphs[name].draw(InkPen(glyphs))
    except Inked:  # the glyph began an outline: that's all we asked
        return True
    return False


class Inked(Exception):
    """Raised by `InkPen` where a glyph starts its first outline."""


class InkPen(DecomposingPen):
    """A pen that stops the drawing where the first outline starts, in the glyph or its parts.

    Every CFF outline starts with a move, so a move is the first thing a pen
    sees of one: fontTools adds it when the glyph's own code leaves it out.
    """

    def moveTo(self, pt: tuple[float, float]) -> None:
        """An outline starts: the glyph draws."""
        raise Inked


def glyph_name_for_each_letter(
    font: TTFont, glyph_ids: Mapping[str, GlyphId] | None
) -> dict[Codepoint, GlyphName]:
    """Which shape draws each letter: from `glyph_ids` when given, else the font's letter table.

    With neither (a symbol font, say) it raises, so the font is marked
    unusable rather than reporting every character as missing.
    """
    # The caller already knows which glyph draws each letter.
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
        if is_letter_code(codepoint)
    }


def is_letter_code(codepoint: int) -> bool:
    """Whether a letter can have this number: a broken font can list one past the last."""
    return 0 <= codepoint <= sys.maxunicode


def letter_named(glyph_name: GlyphName) -> str | None:
    """The one letter a glyph's name says it draws; None for none, several, or a variant.

    A variant ("a.alt", "T.sc") is another shape for the letter, not its own.
    """
    if "." in glyph_name:
        return None
    letter = toUnicode(glyph_name)
    return letter if len(letter) == 1 else None
