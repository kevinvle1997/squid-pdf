from __future__ import annotations

import io

from fontTools.ttLib import TTFont

from squidpdf.core.fonts.catalog import CATALOG, FACES
from squidpdf.core.fonts.names import bare_name, style_of
from squidpdf.core.types import Category, Face, FontDescriptor, LookAlike, Style

_FIXED_WIDTH = 1 << 0
_SERIF = 1 << 1

# OS/2 offsets: sFamilyClass's high byte, then PANOSE's ten bytes from 32 (text faces only).
_FAMILY_CLASS = 30
_PANOSE_FAMILY_TYPE = 32
_PANOSE_SERIF_STYLE = 33
_PANOSE_PROPORTION = 35
_PANOSE_END = 42
_PANOSE_TEXT_FACE = 2
_PANOSE_MONOSPACED = 9
_PANOSE_SERIFS = range(2, 11)
_PANOSE_SANS = range(11, 16)
_SERIF_CLASSES = range(1, 8)
_SANS_CLASS = 8

_NEAREST_STYLE: dict[Style, Style] = {
    "bold-italic": "bold",
    "italic": "regular",
    "bold": "regular",
}

_BROADEST_FAMILY: dict[Category, str] = {
    "sans": "Noto Sans",
    "serif": "Noto Serif",
    "mono": "Noto Sans",
    "handwriting": "Noto Sans",
}

_PLAIN_FAMILY: dict[Category, str] = {
    "sans": "Liberation Sans",
    "serif": "Liberation Serif",
    "mono": "Liberation Mono",
}


def _by_family() -> dict[str, dict[Style, Face]]:
    families: dict[str, dict[Style, Face]] = {}
    for face in CATALOG:
        for name in (face.family, *face.same_widths_as):
            families.setdefault(bare_name(name), {})[face.style] = face
    return families


def _pinned() -> dict[str, Face]:
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
    if font in FACES:
        return LookAlike(FACES[font], same_widths=True)
    pinned_face = _PINNED.get(bare_name(font))
    if pinned_face is not None:
        return LookAlike(pinned_face, same_widths=True)

    style, usual_cut = style_of(font, descriptor)
    family = _BY_FAMILY.get(bare_name(font))
    if family is not None:
        face = _nearest_face(family, style)
        return LookAlike(face, same_widths=usual_cut and face.style == style)

    category = _category_of(descriptor, file_kind)
    face = _nearest_face(_BY_FAMILY[bare_name(_PLAIN_FAMILY[category])], style)
    return LookAlike(face, same_widths=False)


def broadest(face: Face) -> Face:
    family = _BY_FAMILY[bare_name(_BROADEST_FAMILY[face.category])]
    return _nearest_face(family, face.style)


def _nearest_face(family: dict[Style, Face], style: Style) -> Face:
    while style not in family:
        style = _NEAREST_STYLE[style]
    return family[style]


def kind_in_file(file: bytes) -> Category | None:
    """Many PDFs set only /Flags' Nonsymbolic bit, so a serif reads as sans: OS/2 is truer."""
    try:
        os2 = TTFont(io.BytesIO(file), lazy=True).getTableData("OS/2")
    except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font, and CFF has no OS/2)
        return None
    # No table, or one cut short: a trimmed TrueType says nothing of its kind.
    if len(os2) < _PANOSE_END:
        return None
    text_face = os2[_PANOSE_FAMILY_TYPE] == _PANOSE_TEXT_FACE
    serif_style = os2[_PANOSE_SERIF_STYLE]
    if text_face and os2[_PANOSE_PROPORTION] == _PANOSE_MONOSPACED:
        return "mono"
    if text_face and serif_style in _PANOSE_SERIFS:
        return "serif"
    if text_face and serif_style in _PANOSE_SANS:
        return "sans"
    family_class = os2[_FAMILY_CLASS]
    if family_class in _SERIF_CLASSES:
        return "serif"
    if family_class == _SANS_CLASS:
        return "sans"
    return None


def _category_of(descriptor: FontDescriptor | None, file_kind: Category | None) -> Category:
    flags = 0 if descriptor is None else descriptor.flags
    if flags & _FIXED_WIDTH:
        return "mono"
    if flags & _SERIF:
        return "serif"
    if file_kind is not None:
        return file_kind
    # Nothing to go on: most document text is sans.
    return "sans"
