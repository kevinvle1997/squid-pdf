"""What a font can actually draw, as opposed to what it claims.

A subsetted font still lists glyphs it emptied the outlines of: has_glyph and
valid_codepoints report the claim, not reality. The only honest test is asking
the glyph to draw and checking it produces contours.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Mapping

from fontTools.agl import UV2AGL
from fontTools.cffLib import CFFFontSet
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from squidpdf.core.types import Codepoint, GlyphId, GlyphName

# Counted as drawable whether the font maps them or not: tabs and line breaks
# aren't drawn, and the plain space is one text extraction adds between words a
# font never drew a space for. Any other space (no-break, figure, thin...) draws
# nothing yet still needs the font to map it: unmapped, it's drawn as .notdef.
_ALWAYS_DRAWABLE = frozenset({" ", "\t", "\n", "\r"})


# A bare CFF font program starts with this header (major.minor version 1.0);
# anything else handed to Coverage is assumed to be a TrueType/OpenType wrapper.
_BARE_CFF_SIGNATURE = b"\x01\x00"

# The codepoints worth checking for a name-keyed CFF's glyph names: from the
# first printable ASCII character through the end of Supplemental Punctuation,
# covering Latin, Greek, Cyrillic and friends. A document needing coverage
# checked past this is rare enough not to justify scanning the full Unicode
# space on every font load.
_CODEPOINT_SCAN_START = 0x20
_CODEPOINT_SCAN_END = 0x2E00


class Coverage:
    """Which characters an embedded font program can really render.

    Built from the raw bytes as extracted from the PDF, which may be a bare CFF
    (Adobe's compact outline format), a TrueType, or an OpenType wrapper. Bytes
    it can't read (Type1, a font whose letter table has no Unicode) fall back to
    `claimed`, the font engine's own list: the best word left. A TrueType with no
    Unicode letter table can be given `glyph_ids` (letter -> glyph id) instead;
    then only those letters count.
    Results are cached per character because the check runs on every keystroke.
    """

    def __init__(
        self,
        buffer: bytes,
        claimed: Iterable[int] = (),
        glyph_ids: Mapping[str, GlyphId] | None = None,
    ) -> None:
        """Parse a font's raw bytes; `claimed` is what it draws if they won't parse."""
        self._glyph_names: dict[Codepoint, GlyphName] = {}  # filled once loaded
        self._glyphs = None  # glyph set to draw from, once loaded
        self._cache: dict[str, bool] = {}  # per-character result, checked every keystroke
        self._claimed = frozenset(claimed)
        # With glyph_ids the font is written by code, and a space with no code can't be.
        self._always = _ALWAYS_DRAWABLE if glyph_ids is None else frozenset[str]()
        self.usable = False  # True once a parseable font has been loaded
        self._load(buffer, glyph_ids)

    def _load(self, buffer: bytes, glyph_ids: Mapping[str, GlyphId] | None) -> None:
        """Figure out the font's format and parse it, or give up quietly."""
        if not buffer:
            return
        try:
            if buffer[:2] == _BARE_CFF_SIGNATURE:
                self._load_bare_cff(buffer)
            else:
                self._load_sfnt(buffer, glyph_ids)
            self.usable = True
        except Exception:  # noqa: BLE001 (a font we cannot parse is not a crash)
            self.usable = False

    def _load_sfnt(self, buffer: bytes, glyph_ids: Mapping[str, GlyphId] | None) -> None:
        """TrueType or OpenType, mapped by its letter table (cmap) or by `glyph_ids`."""
        font = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
        self._glyph_names = _glyph_name_for_each_letter(font, glyph_ids)
        self._glyphs = font.getGlyphSet()

    def _load_bare_cff(self, buffer: bytes) -> None:
        """A bare CFF, as CIDFontType0 subsets are embedded.

        There is no cmap here, so characters are matched by glyph name using the
        standard Adobe names the charset already carries. That only works for
        name-keyed CFFs; a CID-keyed one carries CID glyph names instead
        (`cid00034`, not `eacute`), which cannot be mapped back to Unicode from
        the font bytes alone. Raised so the font is marked unusable rather than
        silently reporting every character as missing.
        """
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(buffer), None)
        font = cff[cff.fontNames[0]]
        if getattr(font, "ROS", None) is not None:
            raise ValueError("CID-keyed CFF: no Unicode mapping from bytes alone")
        self._glyphs = font.CharStrings
        names = set(font.getGlyphOrder())
        for codepoint in range(_CODEPOINT_SCAN_START, _CODEPOINT_SCAN_END):
            name = _adobe_name(codepoint)
            if name in names:
                self._glyph_names[Codepoint(codepoint)] = name

    def covers(self, ch: str) -> bool:
        """True when this font really puts ink on the page for `ch`, or places its space."""
        if ch in self._always:
            return True
        if not self.usable:
            return ord(ch) in self._claimed
        # A space draws no ink, so being mapped is all it takes.
        if ch.isspace():
            return Codepoint(ord(ch)) in self._glyph_names
        draws = self._cache.get(ch)
        if draws is None:
            draws = self._cache[ch] = self._draws(ch)
        return draws

    def _draws(self, ch: str) -> bool:
        """Ask the glyph itself to draw, and check that it produced any ink."""
        name = self._glyph_names.get(Codepoint(ord(ch)))
        if name is None or self._glyphs is None:
            return False
        try:
            pen = RecordingPen()
            self._glyphs[name].draw(pen)
            return bool(pen.value)
        except Exception:  # noqa: BLE001 (a glyph that will not draw is missing)
            return False

    def drawable(self) -> list[str]:
        """Every character `covers` says draws, spaces included, in code point order."""
        codes = self._glyph_names if self.usable else self._claimed
        chars = (chr(codepoint) for codepoint in codes)
        return sorted(self._always.union(ch for ch in chars if self.covers(ch)))

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that this font cannot draw, in order, deduped."""
        unique = dict.fromkeys(text)  # drops repeats, keeps first-seen order
        return [ch for ch in unique if not self.covers(ch)]


def _glyph_name_for_each_letter(
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
    return {Codepoint(codepoint): name for codepoint, name in cmap.items()}


def _adobe_name(codepoint: int) -> str:
    """Standard Adobe glyph name for a code point, for fonts with no letter table."""
    return UV2AGL.get(codepoint, f"uni{codepoint:04X}")
