"""Which face we ship stands in when a document's own font can't be used.

A PDF usually embeds only the glyphs the document actually used, so whether an
edit is possible in the original face depends on what the user types. When it
isn't, a face from the catalog draws instead: where we can, one whose letters
are exactly as wide as the original's, so nothing on the page moves.
"""

from __future__ import annotations

from squidpdf.core.fonts.catalog import CATALOG, FACES
from squidpdf.core.fonts.constants import (
    BROADEST_FAMILY,
    NEAREST_STYLE,
    PLAIN_FAMILY,
    STYLE_BY_BOLD_ITALIC,
)
from squidpdf.core.types import Category, Face, FontDescriptor, LookAlike, Style

__all__ = [
    "strip_subset",
    "family_and_style",
    "bare_name",
    "style_of",
    "look_alike",
    "broadest",
]

# Bits of a PDF font description's /Flags.
_FIXED_WIDTH = 1 << 0
_SERIF = 1 << 1
_ITALIC = 1 << 6
_FORCE_BOLD = 1 << 18
_BOLD_WEIGHT = 600  # /FontWeight at or past this reads as bold

# Words in a font's name after the family, and what they say about its style.
_BOLD_WORDS = ("bold", "black", "heavy")
_ITALIC_WORDS = ("italic", "oblique", "ital")
# Another weight or width than regular or bold: the letters are wider or narrower.
_OTHER_CUT_WORDS = (
    "light",
    "thin",
    "medium",
    "medi",
    "semi",
    "demi",
    "extra",
    "ultra",
    "black",
    "heavy",
    "narrow",
    "condensed",
    "wide",
)
# Words that can end a spaced name, like "Calibri Bold", and aren't part of the family.
_STYLE_WORDS = {
    "regular",
    "normal",
    "book",
    "bold",
    "italic",
    "oblique",
    "light",
    "medium",
    "semibold",
    "black",
}


def strip_subset(font: str) -> str:
    """`ABCDEE+Calibri-Bold` -> `Calibri-Bold`. Style is kept, only the prefix goes."""
    return font.split("+", 1)[-1]


def family_and_style(font: str) -> tuple[str, str]:
    """A font's name as its family and the style words after it.

    `ABCDEE+Calibri-Bold` -> (`Calibri`, `Bold`); `Arial,BoldItalic` and
    `Calibri Bold` split the same way.
    """
    name = strip_subset(font)
    # "Calibri-Bold" and "Arial,Bold": the style comes after the first dash or comma.
    for mark in ("-", ","):
        if mark in name:
            family, style = name.split(mark, 1)
            return family, style
    # "Calibri Bold": the style is the words at the end that name one.
    words = name.split()
    style_words: list[str] = []
    while len(words) > 1 and words[-1].lower() in _STYLE_WORDS:
        style_words.insert(0, words.pop())
    return " ".join(words), " ".join(style_words)


def bare_name(font: str) -> str:
    """`ABCDEE+Calibri-Bold` -> `calibri`.

    Subset prefixes and style suffixes are noise when looking up a *family*.
    Note this deliberately reads the name; picking a face for a font we don't
    know must not, because names lie: `NimbusRomNo9L` is a serif despite
    containing no "roman". Do not use this to identify one font resource on a
    page, where two different weights share a bare name; use `strip_subset` there.
    """
    family, _style = family_and_style(font)
    return family.replace(" ", "").lower()


def by_family() -> dict[str, dict[Style, Face]]:
    """Each family's faces by style, under its bare name and those of the fonts it matches."""
    families: dict[str, dict[Style, Face]] = {}
    for face in CATALOG:
        for name in (face.family, *face.same_widths_as):
            families.setdefault(bare_name(name), {})[face.style] = face
    return families


def pinned() -> dict[str, Face]:
    """Document fonts only one face stands in for, like CMBX10, by bare name."""
    faces_by_name: dict[str, list[Face]] = {}
    for face in CATALOG:
        for name in face.same_widths_as:
            faces_by_name.setdefault(bare_name(name), []).append(face)
    return {name: faces[0] for name, faces in faces_by_name.items() if len(faces) == 1}


_BY_FAMILY = by_family()
_PINNED = pinned()


def look_alike(font: str, descriptor: FontDescriptor | None = None) -> LookAlike:
    """The face that stands in for a document font, in its style.

    A face we ship by its exact name is itself, and so is one a document font's
    name pins, cut and all (CMBX10). A family we know gets the face with the
    same letter widths. Anything else gets a plain face of its kind,
    judged by the PDF's own description of the font (`descriptor`), not its
    name. The style comes from both.
    """
    # Asked for by name, as an insert does.
    if font in FACES:
        return LookAlike(FACES[font], same_widths=True)
    # A name that says the cut as well as the family, as TeX's do: that one face.
    pinned_face = _PINNED.get(bare_name(font))
    if pinned_face is not None:
        return LookAlike(pinned_face, same_widths=True)

    style, usual_cut = style_of(font, descriptor)
    family = _BY_FAMILY.get(bare_name(font))  # None: a family we don't ship
    # A family we know: the same letter widths, if it's a cut we have.
    if family is not None:
        face = nearest_face(family, style)
        return LookAlike(face, same_widths=usual_cut and face.style == style)

    # Unknown: a plain face of its kind.
    category = category_of(descriptor)
    face = nearest_face(_BY_FAMILY[bare_name(PLAIN_FAMILY[category])], style)
    return LookAlike(face, same_widths=False)


def broadest(face: Face) -> Face:
    """The face with the most letters, of the same kind and style as `face`."""
    family = _BY_FAMILY[bare_name(BROADEST_FAMILY[face.category])]
    return nearest_face(family, face.style)


def nearest_face(family: dict[Style, Face], style: Style) -> Face:
    """The family's face in `style`, or the nearest style it has."""
    while style not in family:
        style = NEAREST_STYLE[style]
    return family[style]


def style_of(font: str, descriptor: FontDescriptor | None) -> tuple[Style, bool]:
    """The style a font's name and description say it is, and whether it's a usual cut.

    A usual cut is plain regular, bold or italic: a light or narrow cut of a
    family we know still has other letter widths than the face we ship.
    """
    _family_name, style_words = family_and_style(font)
    style_text = style_words.lower()
    bold = any(word in style_text for word in _BOLD_WORDS)
    italic = any(word in style_text for word in _ITALIC_WORDS)
    usual_cut = not any(word in style_text for word in _OTHER_CUT_WORDS)
    if descriptor is not None:
        heavy = descriptor.weight is not None and descriptor.weight >= _BOLD_WEIGHT
        bold = bold or bool(descriptor.flags & _FORCE_BOLD) or heavy
        italic = italic or bool(descriptor.flags & _ITALIC) or descriptor.italic_angle != 0
    return STYLE_BY_BOLD_ITALIC[(bold, italic)], usual_cut


def category_of(descriptor: FontDescriptor | None) -> Category:
    """Serif, fixed width or sans, as the PDF describes the font; sans when it doesn't."""
    # Nothing to go on: most document text is sans.
    if descriptor is None:
        return "sans"
    # Every letter as wide as the next.
    if descriptor.flags & _FIXED_WIDTH:
        return "mono"
    # Serif set, fixed width not.
    if descriptor.flags & _SERIF:
        return "serif"
    # Described, and neither.
    return "sans"
