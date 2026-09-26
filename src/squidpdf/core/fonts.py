"""The fonts we ship, and which one stands in when a document's own can't be used.

A PDF usually embeds only the glyphs the document actually used, so whether an
edit is possible in the original face depends on what the user types. When it
isn't, a face from this catalog draws instead: where we can, one whose letters
are exactly as wide as the original's, so nothing on the page moves.
"""

from __future__ import annotations

from functools import cache
from importlib import resources

from squidpdf.core.types import Category, Face, FontDescriptor, LookAlike, Style

_OFL = "OFL-1.1"  # every face we ship is under the SIL Open Font License

_ALL_STYLES: tuple[Style, ...] = ("regular", "bold", "italic", "bold-italic")

# How each style is written in a face's name; its file's name drops the spaces.
_STYLE_NAMES: dict[Style, str] = {
    "regular": "Regular",
    "bold": "Bold",
    "italic": "Italic",
    "bold-italic": "Bold Italic",
}


def _family(
    family: str,
    category: Category,
    same_widths_as: tuple[str, ...] = (),
    styles: tuple[Style, ...] = _ALL_STYLES,
) -> tuple[Face, ...]:
    """Every face of one family, named and filed the same way."""
    file_stem = family.replace(" ", "")
    return tuple(
        Face(
            name=f"{family} {_STYLE_NAMES[style]}",
            family=family,
            style=style,
            category=category,
            file=f"{file_stem}-{_STYLE_NAMES[style].replace(' ', '')}.ttf",
            license=_OFL,
            same_widths_as=same_widths_as,
        )
        for style in styles
    )


# Every face we ship. Where it came from and its version: `fonts/README.md`.
CATALOG: tuple[Face, ...] = (
    # Same letter widths as the fonts documents most often name but don't embed.
    *_family(
        "Liberation Sans",
        "sans",
        ("Arial", "ArialMT", "Helvetica", "Arimo", "Nimbus Sans", "NimbusSanL"),
    ),
    *_family(
        "Liberation Serif",
        "serif",
        (
            "Times New Roman",
            "TimesNewRomanPSMT",
            "TimesNewRomanPS",
            "Times",
            "Times Roman",
            "Tinos",
            "Nimbus Roman",
            "NimbusRomNo9L",
        ),
    ),
    *_family(
        "Liberation Mono",
        "mono",
        ("Courier New", "CourierNewPSMT", "CourierNewPS", "Courier", "Cousine", "NimbusMonL"),
    ),
    *_family("Carlito", "sans", ("Calibri",)),
    *_family("Caladea", "serif", ("Cambria",)),
    # The broadest: letters a look-alike lacks (Greek, Cyrillic, more accents) draw in these.
    *_family("Noto Sans", "sans"),
    *_family("Noto Serif", "serif"),
    # More choice for new text.
    *_family("Inter", "sans"),
    *_family("Roboto", "sans"),
    *_family("Lato", "sans"),
    *_family("EB Garamond", "serif"),
    *_family("IBM Plex Serif", "serif"),
    *_family("IBM Plex Mono", "mono"),
    *_family("Caveat", "handwriting", styles=("regular", "bold")),
    *_family("Great Vibes", "handwriting", styles=("regular",)),
)

# Each face by the name an insert asks for it by.
FACES: dict[str, Face] = {face.name: face for face in CATALOG}

# The face that draws a letter a look-alike lacks, by the look-alike's kind of font.
_BROADEST: dict[Category, str] = {
    "sans": "Noto Sans",
    "serif": "Noto Serif",
    "mono": "Noto Sans",
    "handwriting": "Noto Sans",
}

# A document font we know nothing about gets a face of its kind; letter widths will differ.
_PLAIN: dict[Category, str] = {
    "sans": "Liberation Sans",
    "serif": "Liberation Serif",
    "mono": "Liberation Mono",
}

# A style a family lacks falls back one step at a time.
_NEAREST_STYLE: dict[Style, Style] = {
    "bold-italic": "bold",
    "italic": "regular",
    "bold": "regular",
}

# Bits of a PDF font description's /Flags.
_FIXED_WIDTH = 1 << 0
_SERIF = 1 << 1
_ITALIC = 1 << 6
_FORCE_BOLD = 1 << 18
_BOLD_WEIGHT = 600  # /FontWeight at or past this reads as bold

# A style by whether it's (bold, italic).
_STYLES: dict[tuple[bool, bool], Style] = {
    (False, False): "regular",
    (True, False): "bold",
    (False, True): "italic",
    (True, True): "bold-italic",
}

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


def _split(font: str) -> tuple[str, str]:
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
    family, _style = _split(font)
    return family.replace(" ", "").lower()


def _by_family() -> dict[str, dict[Style, Face]]:
    """Each family's faces by style, under its bare name and those of the fonts it matches."""
    families: dict[str, dict[Style, Face]] = {}
    for face in CATALOG:
        for name in (face.family, *face.same_widths_as):
            families.setdefault(bare_name(name), {})[face.style] = face
    return families


_BY_FAMILY = _by_family()


def look_alike(font: str, descriptor: FontDescriptor | None = None) -> LookAlike:
    """The face that stands in for a document font, in its style.

    A face we ship by its exact name is itself. A family we know gets the face
    with the same letter widths. Anything else gets a plain face of its kind,
    judged by the PDF's own description of the font (`descriptor`), not its
    name. The style comes from both.
    """
    # Asked for by name, as an insert does.
    if font in FACES:
        return LookAlike(FACES[font], same_widths=True)

    style, usual_cut = _style_of(font, descriptor)
    family = _BY_FAMILY.get(bare_name(font))  # None: a family we don't ship
    # A family we know: the same letter widths, if it's a cut we have.
    if family is not None:
        face = _nearest(family, style)
        return LookAlike(face, same_widths=usual_cut and face.style == style)

    # Unknown: a plain face of its kind.
    category = _category_of(descriptor)
    face = _nearest(_BY_FAMILY[bare_name(_PLAIN[category])], style)
    return LookAlike(face, same_widths=False)


def broadest(face: Face) -> Face:
    """The face with the most letters, of the same kind and style as `face`."""
    family = _BY_FAMILY[bare_name(_BROADEST[face.category])]
    return _nearest(family, face.style)


@cache
def face_bytes(face: Face) -> bytes:
    """The face's font file, as shipped in the package."""
    return resources.files("squidpdf").joinpath("fonts", face.file).read_bytes()


def _nearest(family: dict[Style, Face], style: Style) -> Face:
    """The family's face in `style`, or the nearest style it has."""
    while style not in family:
        style = _NEAREST_STYLE[style]
    return family[style]


def _style_of(font: str, descriptor: FontDescriptor | None) -> tuple[Style, bool]:
    """The style a font's name and description say it is, and whether it's a usual cut.

    A usual cut is plain regular, bold or italic: a light or narrow cut of a
    family we know still has other letter widths than the face we ship.
    """
    _family_name, style_words = _split(font)
    words = style_words.lower()
    bold = any(word in words for word in _BOLD_WORDS)
    italic = any(word in words for word in _ITALIC_WORDS)
    usual_cut = not any(word in words for word in _OTHER_CUT_WORDS)
    if descriptor is not None:
        heavy = descriptor.weight is not None and descriptor.weight >= _BOLD_WEIGHT
        bold = bold or bool(descriptor.flags & _FORCE_BOLD) or heavy
        italic = italic or bool(descriptor.flags & _ITALIC) or descriptor.italic_angle != 0
    return _STYLES[(bold, italic)], usual_cut


def _category_of(descriptor: FontDescriptor | None) -> Category:
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
