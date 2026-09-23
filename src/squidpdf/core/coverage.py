"""What a font can actually draw, as opposed to what it claims.

A subsetted font still lists glyphs it emptied the outlines of — has_glyph and
valid_codepoints report the claim, not reality. The only honest test is asking
the glyph to draw and checking it produces contours.
"""

from __future__ import annotations

import io

from fontTools.cffLib import CFFFontSet
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

# Characters that legitimately draw nothing.
_BLANK = {" ", "\t", "\n", "\r", " ", " ", " "}


class Coverage:
    """Which characters an embedded font program can really render.

    Built from the raw bytes as extracted from the PDF, which may be a bare CFF,
    a TrueType, or an OpenType wrapper. Results are cached per character because
    the check runs on every keystroke.
    """

    def __init__(self, buffer: bytes) -> None:
        """Parse a font's raw bytes so `covers()` and `missing()` can be asked."""
        self._cmap: dict[int, str] = {}  # codepoint -> glyph name, once loaded
        self._glyphs = None  # glyph set to draw from, once loaded
        self._cache: dict[str, bool] = {}  # per-character result, checked every keystroke
        self._usable = False  # True once a parseable font has been loaded
        self._load(buffer)

    def _load(self, buffer: bytes) -> None:
        """Figure out the font's format and parse it, or give up quietly."""
        if not buffer:
            return
        try:
            if buffer[:2] == b"\x01\x00":
                self._load_bare_cff(buffer)
            else:
                self._load_sfnt(buffer)
            self._usable = True
        except Exception:  # noqa: BLE001 — a font we cannot parse is not a crash
            self._usable = False

    def _load_sfnt(self, buffer: bytes) -> None:
        """TrueType or OpenType, where a cmap table gives us character mapping."""
        tt = TTFont(io.BytesIO(buffer), fontNumber=0, lazy=True)
        self._cmap = dict(tt.getBestCmap())
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
        for cp in range(0x20, 0x2E00):
            name = _adobe_name(cp)
            if name is not None and name in names:
                self._cmap[cp] = name

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
        name = self._cmap.get(ord(ch))
        if name is None or self._glyphs is None:
            return False
        try:
            pen = RecordingPen()
            self._glyphs[name].draw(pen)
            return bool(pen.value)
        except Exception:  # noqa: BLE001 — a glyph that will not draw is missing
            return False

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


def _adobe_name(cp: int) -> str | None:
    """Standard Adobe glyph name for a codepoint, for fonts with no cmap."""
    from fontTools.agl import UV2AGL

    if cp in UV2AGL:
        return UV2AGL[cp]
    return f"uni{cp:04X}"
