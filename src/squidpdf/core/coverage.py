"""What a font can actually draw, as opposed to what it claims.

A subsetted font still lists glyphs it emptied the outlines of — has_glyph and
valid_codepoints report the claim, not reality. The only honest test is asking
the glyph to draw and checking it produces contours.
"""

from __future__ import annotations

import io

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
    a TrueType, or an OpenType wrapper. Results are cached per character because
    the check runs on every keystroke.
    """

    def __init__(self, buffer: bytes) -> None:
        """Parse a font's raw bytes so `covers()` and `missing()` can be asked."""
        self._glyph_names: dict[int, str] = {}  # codepoint -> glyph name, once loaded
        self._glyphs = None  # glyph set to draw from, once loaded
        self._cache: dict[str, bool] = {}  # per-character result, checked every keystroke
        self._usable = False  # True once a parseable font has been loaded
        self._load(buffer)

    def _load(self, buffer: bytes) -> None:
        """Figure out the font's format and parse it, or give up quietly."""
        if not buffer:
            return
        try:
            if buffer[:2] == _BARE_CFF_SIGNATURE:
                self._load_bare_cff(buffer)
            else:
                self._load_sfnt(buffer)
            self._usable = True
        except Exception:  # noqa: BLE001 — a font we cannot parse is not a crash
            self._usable = False

    def _load_sfnt(self, buffer: bytes) -> None:
        """TrueType or OpenType, where a cmap table gives us character mapping."""
        tt = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
        self._glyph_names = dict(tt.getBestCmap())
        self._glyphs = tt.getGlyphSet()

    def _load_bare_cff(self, buffer: bytes) -> None:
        """A bare CFF, as CIDFontType0 subsets are embedded.

        There is no cmap here, so characters are matched by glyph name using the
        standard Adobe names the charset already carries. That only works for
        name-keyed CFFs; a CID-keyed one carries CID glyph names instead
        (`cid00034`, not `eacute`), which cannot be mapped back to Unicode from
        the font bytes alone — raised so the font is marked unusable rather than
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
        if ch in _BLANK:
            return True
        if not self._usable:
            return True  # unparseable: do not claim a problem we cannot prove
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
        except Exception:  # noqa: BLE001 — a glyph that will not draw is missing
            return False

    def drawable(self) -> list[str] | None:
        """Every character the font maps that really draws, or None if it would not parse."""
        if not self._usable:
            return None
        return [chr(cp) for cp in sorted(self._glyph_names) if self.covers(chr(cp))]

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
