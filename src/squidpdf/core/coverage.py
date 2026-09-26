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

# Characters that legitimately draw nothing.
_BLANK = {" ", "\t", "\n", "\r", " ", " ", " "}


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

    Built from the raw bytes as extracted from the PDF, which may be a bare CFF,
    a TrueType, or an OpenType wrapper. Bytes it can't read (Type1, a symbol-only
    cmap) fall back to `claimed`, the font engine's own list: the best word left.
    A TrueType with no Unicode cmap can be handed `glyphs`, letter to glyph id,
    from whoever knows how the document reaches them; then only those letters count.
    Results are cached per character because the check runs on every keystroke.
    """

    def __init__(
        self,
        buffer: bytes,
        claimed: Iterable[int] = (),
        glyphs: Mapping[str, int] | None = None,
    ) -> None:
        """Parse a font's raw bytes; `claimed` is what it draws if they won't parse."""
        self._glyph_names: dict[int, str] = {}  # codepoint -> glyph name, once loaded
        self._glyphs = None  # glyph set to draw from, once loaded
        self._cache: dict[str, bool] = {}  # per-character result, checked every keystroke
        self._claimed = frozenset(claimed)
        # Given glyphs, the blanks are the whitespace it maps: a thin space draws nothing too.
        self._blanks = _BLANK if glyphs is None else {ch for ch in glyphs if ch.isspace()}
        self.usable = False  # True once a parseable font has been loaded
        self._load(buffer, glyphs)

    def _load(self, buffer: bytes, glyphs: Mapping[str, int] | None) -> None:
        """Figure out the font's format and parse it, or give up quietly."""
        if not buffer:
            return
        try:
            if buffer[:2] == _BARE_CFF_SIGNATURE:
                self._load_bare_cff(buffer)
            else:
                self._load_sfnt(buffer, glyphs)
            self.usable = True
        except Exception:  # noqa: BLE001 (a font we cannot parse is not a crash)
            self.usable = False

    def _load_sfnt(self, buffer: bytes, glyphs: Mapping[str, int] | None) -> None:
        """TrueType or OpenType, where a cmap table, or `glyphs`, maps characters.

        A font with no Unicode cmap (a symbol font, say) and no `glyphs` raises, so
        it is marked unusable rather than reporting every character as missing.
        """
        tt = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
        if glyphs is None:
            cmap = tt.getBestCmap()
            if cmap is None:
                raise ValueError("no Unicode cmap: characters can't be matched to glyphs")
        else:
            order = tt.getGlyphOrder()
            cmap = {ord(ch): order[gid] for ch, gid in glyphs.items() if gid < len(order)}
        self._glyph_names = dict(cmap)
        self._glyphs = tt.getGlyphSet()

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
        for cp in range(_CODEPOINT_SCAN_START, _CODEPOINT_SCAN_END):
            name = _adobe_name(cp)
            if name in names:
                self._glyph_names[cp] = name

    def covers(self, ch: str) -> bool:
        """True when this font will actually put ink on the page for `ch`."""
        if ch in self._blanks:
            return True
        if not self.usable:
            return ord(ch) in self._claimed
        hit = self._cache.get(ch)
        if hit is None:
            hit = self._cache[ch] = self._draws(ch)
        return hit

    def _draws(self, ch: str) -> bool:
        """Ask the glyph itself to draw, and check that it produced any ink."""
        name = self._glyph_names.get(ord(ch))
        if name is None or self._glyphs is None:
            return False
        try:
            pen = RecordingPen()
            self._glyphs[name].draw(pen)
            return bool(pen.value)
        except Exception:  # noqa: BLE001 (a glyph that will not draw is missing)
            return False

    def drawable(self) -> list[str]:
        """Every character `covers` says draws, blanks included, in code point order."""
        codes = self._glyph_names if self.usable else self._claimed
        return sorted(self._blanks.union(chr(cp) for cp in codes if self.covers(chr(cp))))

    def missing(self, text: str) -> list[str]:
        """Characters `text` needs that this font cannot draw, in order, deduped."""
        out: list[str] = []
        seen: set[str] = set()
        for ch in text:
            if ch in seen:
                continue
            seen.add(ch)
            if not self.covers(ch):
                out.append(ch)
        return out


def _adobe_name(cp: int) -> str:
    """Standard Adobe glyph name for a codepoint, for fonts with no cmap."""
    return UV2AGL.get(cp, f"uni{cp:04X}")
