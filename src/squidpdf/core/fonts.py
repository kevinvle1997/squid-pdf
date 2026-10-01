"""The fonts we ship, and which one stands in when a document's own can't be used.

A PDF usually embeds only the glyphs the document actually used, so whether an
edit is possible in the original face depends on what the user types. When it
isn't, a face from this catalog draws instead: where we can, one whose letters
are exactly as wide as the original's, so nothing on the page moves.
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from importlib import resources

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core.types import Category, Face, FontDescriptor, LookAlike, Style

__all__ = [
    "CATALOG",
    "FACES",
    "strip_subset",
    "bare_name",
    "look_alike",
    "broadest",
    "face_bytes",
    "trimmed",
]

_OFL = "OFL-1.1"  # the SIL Open Font License, which most faces we ship are under
_GUST = "GUST-Font-License"  # Latin Modern's: the LaTeX Project Public License, plus a request
# GNU FreeFont's: a document that embeds it stays the user's, under no licence of ours.
_FREEFONT = "GPL-3.0-or-later WITH Font-exception-2.0"

_ALL_STYLES: tuple[Style, ...] = ("regular", "bold", "italic", "bold-italic")

# How each style is written in a face's name; its file's name drops the spaces.
_STYLE_NAMES: dict[Style, str] = {
    "regular": "Regular",
    "bold": "Bold",
    "italic": "Italic",
    "bold-italic": "Bold Italic",
}


def family_faces(
    family: str,
    category: Category,
    *,
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


@dataclass(frozen=True, slots=True)
class Cut:
    """One face of a family whose files aren't named our way, as its own files say."""

    style: Style
    called: str  # the style as the font's own name says it: "Oblique", "10 Bold"
    file: str
    same_widths_as: tuple[str, ...] = ()  # document fonts that are this cut, like CMBX10


def cut_faces(
    family: str,
    category: Category,
    *,
    license: str,
    cuts: tuple[Cut, ...],
    same_widths_as: tuple[str, ...] = (),
) -> tuple[Face, ...]:
    """A family's faces, each named and filed as its source ships it."""
    return tuple(
        Face(
            name=f"{family} {cut.called}",
            family=family,
            style=cut.style,
            category=category,
            file=cut.file,
            license=license,
            same_widths_as=(*same_widths_as, *cut.same_widths_as),
        )
        for cut in cuts
    )


# Every face we ship. Where it came from and its version: `fonts/README.md`.
CATALOG: tuple[Face, ...] = (
    # Same letter widths as the fonts documents most often name but don't embed.
    *family_faces(
        "Liberation Sans",
        "sans",
        same_widths_as=("Arial", "ArialMT", "Helvetica", "Arimo", "Nimbus Sans", "NimbusSanL"),
    ),
    *family_faces(
        "Liberation Serif",
        "serif",
        same_widths_as=(
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
    *family_faces(
        "Liberation Mono",
        "mono",
        same_widths_as=(
            "Courier New",
            "CourierNewPSMT",
            "CourierNewPS",
            "Courier",
            "Cousine",
            "NimbusMonL",
        ),
    ),
    *family_faces("Carlito", "sans", same_widths_as=("Calibri",)),
    *family_faces("Caladea", "serif", same_widths_as=("Cambria",)),
    # The broadest: letters a look-alike lacks (Greek, Cyrillic, more accents) draw in these.
    *family_faces("Noto Sans", "sans"),
    *family_faces("Noto Serif", "serif"),
    # The typeface a document names, so its widths match. Offered for new text too.
    *family_faces("Poppins", "sans"),
    *family_faces("Open Sans", "sans"),
    *family_faces("Montserrat", "sans"),
    *family_faces("Nunito", "sans"),
    *family_faces("PT Sans", "sans"),
    *family_faces("PT Serif", "serif"),
    *cut_faces(
        "FreeSans",
        "sans",
        license=_FREEFONT,
        cuts=(
            Cut("regular", "Regular", "FreeSans.ttf"),
            Cut("bold", "Bold", "FreeSansBold.ttf", ("FreeSansBold",)),
            Cut("italic", "Oblique", "FreeSansOblique.ttf", ("FreeSansOblique",)),
            Cut(
                "bold-italic",
                "Bold Oblique",
                "FreeSansBoldOblique.ttf",
                ("FreeSansBoldOblique",),
            ),
        ),
    ),
    *cut_faces(
        "FreeSerif",
        "serif",
        license=_FREEFONT,
        cuts=(
            Cut("regular", "Regular", "FreeSerif.ttf"),
            Cut("bold", "Bold", "FreeSerifBold.ttf", ("FreeSerifBold",)),
            Cut("italic", "Italic", "FreeSerifItalic.ttf", ("FreeSerifItalic",)),
            Cut(
                "bold-italic",
                "Bold Italic",
                "FreeSerifBoldItalic.ttf",
                ("FreeSerifBoldItalic",),
            ),
        ),
    ),
    # LaTeX's Computer Modern, redrawn with the same widths. TeX names a font
    # by its cut and size (CMBX10: bold, 10 pt design), so each name pins one face.
    *cut_faces(
        "Latin Modern Roman 10",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman10",),
        cuts=(
            Cut("regular", "Regular", "lmroman10-regular.otf", ("CMR10", "SFRM1000")),
            Cut("bold", "Bold", "lmroman10-bold.otf", ("CMBX10", "SFBX1000")),
            Cut("italic", "Italic", "lmroman10-italic.otf", ("CMTI10", "SFTI1000")),
            Cut(
                "bold-italic",
                "Bold Italic",
                "lmroman10-bolditalic.otf",
                ("CMBXTI10", "SFBI1000"),
            ),
        ),
    ),
    *cut_faces(
        "Latin Modern Roman 12",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman12",),
        cuts=(
            Cut("regular", "Regular", "lmroman12-regular.otf", ("CMR12", "SFRM1200")),
            Cut("bold", "Bold", "lmroman12-bold.otf", ("CMBX12", "SFBX1200")),
            Cut("italic", "Italic", "lmroman12-italic.otf", ("CMTI12", "SFTI1200")),
        ),
    ),
    *cut_faces(
        "Latin Modern Roman 17",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman17",),
        cuts=(Cut("regular", "Regular", "lmroman17-regular.otf", ("CMR17", "SFRM1728")),),
    ),
    *cut_faces(
        "Latin Modern Roman Caps 10",
        "serif",
        license=_GUST,
        same_widths_as=("LMRomanCaps10",),
        cuts=(Cut("regular", "Regular", "lmromancaps10-regular.otf", ("CMCSC10", "SFCC1000")),),
    ),
    *cut_faces(
        "Latin Modern Mono 10",
        "mono",
        license=_GUST,
        same_widths_as=("LMMono10",),
        cuts=(
            Cut("regular", "Regular", "lmmono10-regular.otf", ("CMTT10", "SFTT1000")),
            Cut("italic", "Italic", "lmmono10-italic.otf", ("CMITT10", "SFIT1000")),
        ),
    ),
    # More choice for new text.
    *family_faces("Inter", "sans"),
    *family_faces("Roboto", "sans"),
    *family_faces("Lato", "sans"),
    *family_faces("EB Garamond", "serif"),
    *family_faces("IBM Plex Serif", "serif"),
    *family_faces("IBM Plex Mono", "mono"),
    *family_faces("Caveat", "handwriting", styles=("regular", "bold")),
    *family_faces("Great Vibes", "handwriting", styles=("regular",)),
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
    face = nearest_face(_BY_FAMILY[bare_name(_PLAIN[category])], style)
    return LookAlike(face, same_widths=False)


def broadest(face: Face) -> Face:
    """The face with the most letters, of the same kind and style as `face`."""
    family = _BY_FAMILY[bare_name(_BROADEST[face.category])]
    return nearest_face(family, face.style)


@cache
def face_bytes(face: Face) -> bytes:
    """The face's font file, as shipped in the package."""
    return resources.files("squidpdf").joinpath("fonts", face.file).read_bytes()


def trimmed(font_file: bytes, letters: Iterable[str]) -> bytes:
    """A font file we added whole, cut down to `letters`."""
    options = Options(
        hinting=True,  # keeps small text crisp on screen, for a few KB
        layout_features=[],  # the PDF places each letter itself: no ligatures or kerning
        retain_gids=True,  # text already on the page points at its glyphs by number
        # FontForge's timestamps: nothing draws with them, and fontTools can't cut them.
        drop_tables=[*Options().drop_tables, "FFTM"],
    )
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=[ord(ch) for ch in letters])
    font = TTFont(io.BytesIO(font_file))
    subsetter.subset(font)
    cut = io.BytesIO()
    font.save(cut)
    return cut.getvalue()


def nearest_face(family: dict[Style, Face], style: Style) -> Face:
    """The family's face in `style`, or the nearest style it has."""
    while style not in family:
        style = _NEAREST_STYLE[style]
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
    return _STYLES[(bold, italic)], usual_cut


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
