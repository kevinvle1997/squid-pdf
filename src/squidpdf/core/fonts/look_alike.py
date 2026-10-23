"""A document font's look-alike: the face we ship we'd use when its own can't be.

A PDF usually embeds only the glyphs the document actually used, so whether an
edit is possible in the original face depends on what the user types. When it
isn't, a face from the catalog draws instead: where we can, one whose letters
are exactly as wide as the original's, so nothing on the page moves.
"""

from __future__ import annotations

import io

from fontTools.ttLib import TTFont

from squidpdf.core.fonts.catalog import CATALOG, FACES
from squidpdf.core.fonts.names import bare_name, style_of
from squidpdf.core.types import Category, Face, FontDescriptor, LookAlike, Style

# Bits of a PDF font description's /Flags.
_FIXED_WIDTH = 1 << 0
_SERIF = 1 << 1

# Where the OS/2 table says a font's kind, by byte: sFamilyClass's high byte, then PANOSE, ten
# bytes from 32, whose digits mean this only for text faces.
_FAMILY_CLASS = 30
_PANOSE_FAMILY_TYPE = 32
_PANOSE_SERIF_STYLE = 33
_PANOSE_PROPORTION = 35
_PANOSE_END = 42
_PANOSE_TEXT_FACE = 2  # bFamilyType "Latin Text"
_PANOSE_MONOSPACED = 9  # bProportion
_PANOSE_SERIFS = range(
    2, 11
)  # bSerifStyle: cove, square, thin, triangle, exaggerated and the rest
_PANOSE_SANS = range(
    11, 16
)  # bSerifStyle: normal, obtuse and perpendicular sans, flared, rounded
# sFamilyClass's high byte, for a file whose PANOSE is left blank.
_SERIF_CLASSES = range(
    1, 8
)  # oldstyle, transitional, modern, clarendon, slab and freeform serifs
_SANS_CLASS = 8

# A style a family lacks falls back one step at a time.
_NEAREST_STYLE: dict[Style, Style] = {
    "bold-italic": "bold",
    "italic": "regular",
    "bold": "regular",
}

# The family that draws a letter a look-alike lacks, by the look-alike's kind of font.
_BROADEST_FAMILY: dict[Category, str] = {
    "sans": "Noto Sans",
    "serif": "Noto Serif",
    "mono": "Noto Sans",
    "handwriting": "Noto Sans",
}

# A document font we know nothing about gets a family of its kind; letter widths will differ.
_PLAIN_FAMILY: dict[Category, str] = {
    "sans": "Liberation Sans",
    "serif": "Liberation Serif",
    "mono": "Liberation Mono",
}


def _by_family() -> dict[str, dict[Style, Face]]:
    """Each family's faces by style, under its bare name and those of the fonts it matches."""
    families: dict[str, dict[Style, Face]] = {}
    for face in CATALOG:
        for name in (face.family, *face.same_widths_as):
            families.setdefault(bare_name(name), {})[face.style] = face
    return families


def _pinned() -> dict[str, Face]:
    """Document fonts only one face is the look-alike for, like CMBX10, by bare name."""
    faces_by_name: dict[str, list[Face]] = {}
    for face in CATALOG:
        for name in face.same_widths_as:
            faces_by_name.setdefault(bare_name(name), []).append(face)
    return {name: faces[0] for name, faces in faces_by_name.items() if len(faces) == 1}


_BY_FAMILY = _by_family()
_PINNED = _pinned()


def look_alike(
    font: str, descriptor: FontDescriptor | None = None, *, file_kind: Category | None = None
) -> LookAlike:
    """A document font's look-alike, in its style: the face we ship we'd use for it.

    A face we ship by its exact name is itself, and so is one a document font's
    name pins, cut and all (CMBX10). A family we know gets the face with the
    same letter widths. Anything else gets a plain face of its kind, judged by
    the PDF's own description of the font (`descriptor`), then by its file's
    (`file_kind`, from `kind_in_file`), never by its name. The style comes from both.
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
        face = _nearest_face(family, style)
        return LookAlike(face, same_widths=usual_cut and face.style == style)

    # Unknown: a plain face of its kind.
    category = _category_of(descriptor, file_kind)
    face = _nearest_face(_BY_FAMILY[bare_name(_PLAIN_FAMILY[category])], style)
    return LookAlike(face, same_widths=False)


def broadest(face: Face) -> Face:
    """The face with the most letters, of the same kind and style as `face`."""
    family = _BY_FAMILY[bare_name(_BROADEST_FAMILY[face.category])]
    return _nearest_face(family, face.style)


def _nearest_face(family: dict[Style, Face], style: Style) -> Face:
    """The family's face in `style`, or the nearest style it has."""
    while style not in family:
        style = _NEAREST_STYLE[style]
    return family[style]


def kind_in_file(file: bytes) -> Category | None:
    """Serif, fixed width or sans, as a font file's own OS/2 table says; None when it doesn't.

    Many PDFs set only /Flags' Nonsymbolic bit, whatever the font looks like, so a
    serif read as sans. The file's PANOSE and family class are its maker's word.
    """
    try:
        os2 = TTFont(io.BytesIO(file), lazy=True).getTableData("OS/2")
    except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font, and CFF has no OS/2)
        return None
    # No table, or one cut short: a trimmed TrueType says nothing of its kind.
    if len(os2) < _PANOSE_END:
        return None
    text_face = os2[_PANOSE_FAMILY_TYPE] == _PANOSE_TEXT_FACE
    serif_style = os2[_PANOSE_SERIF_STYLE]
    # Every letter as wide as the next.
    if text_face and os2[_PANOSE_PROPORTION] == _PANOSE_MONOSPACED:
        return "mono"
    # A text face's serif style, when its PANOSE gives one.
    if text_face and serif_style in _PANOSE_SERIFS:
        return "serif"
    if text_face and serif_style in _PANOSE_SANS:
        return "sans"
    family_class = os2[_FAMILY_CLASS]
    if family_class in _SERIF_CLASSES:
        return "serif"
    if family_class == _SANS_CLASS:
        return "sans"
    # PANOSE and class both left blank, as many free fonts leave them.
    return None


def _category_of(descriptor: FontDescriptor | None, file_kind: Category | None) -> Category:
    """Serif, fixed width or sans, as the PDF or the font file describes it; sans otherwise."""
    flags = 0 if descriptor is None else descriptor.flags
    # Every letter as wide as the next.
    if flags & _FIXED_WIDTH:
        return "mono"
    # Serif set, fixed width not.
    if flags & _SERIF:
        return "serif"
    # The PDF says neither, but the font's own file can.
    if file_kind is not None:
        return file_kind
    # Nothing to go on: most document text is sans.
    return "sans"
