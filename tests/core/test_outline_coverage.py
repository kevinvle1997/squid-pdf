"""Reading whether a glyph has an outline gives the same letters as drawing it."""

from __future__ import annotations

import io

import pytest
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.fonts.coverage import Coverage
from tests.helpers import assert_equal

# Two of each format we ship: TrueType outlines (the largest and a common one)
# and CFF, Adobe's compact outlines. Drawing every face takes too long for the
# suite; the PR that brought the outline read in compared all of them.
_SAMPLED = [
    "FreeSerif Regular",
    "Liberation Sans Regular",
    "Latin Modern Roman 10 Regular",
    "Latin Modern Mono 10 Italic",
]


def _drawn_letters(font_file: bytes) -> list[str]:
    """Every letter the font maps that draws any ink when drawn in full, spaces as mapped."""
    font = TTFont(io.BytesIO(font_file), lazy=True)
    glyphs = font.getGlyphSet()
    drawn = {" "}  # the plain space always counts, mapped or not
    for codepoint, name in font["cmap"].getBestCmap().items():
        pen = DecomposingRecordingPen(glyphs)
        glyphs[name].draw(pen)
        if chr(codepoint).isspace() or pen.value:
            drawn.add(chr(codepoint))
    return sorted(drawn)


@pytest.mark.parametrize("face", _SAMPLED)
def test_reading_the_outline_finds_the_letters_drawing_does(face):
    font_file = face_bytes(FACES[face])
    assert_equal(Coverage(font_file).drawable(), _drawn_letters(font_file), f"{face}'s letters")
