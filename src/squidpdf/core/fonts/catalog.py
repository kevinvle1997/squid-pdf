"""The fonts we ship: every face, named and filed, and its file.

Data about files in `squidpdf/fonts/`, not tuning: where each came from and its
version is `fonts/README.md`. Which one is a document font's look-alike is
`look_alike.py`'s, and which one draws in its place, `substitute.py`'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

from squidpdf.core.fonts.constants import STYLE_NAMES
from squidpdf.core.types import Category, Face, Style

__all__ = [
    "CATALOG",
    "FACES",
    "face_bytes",
]

_OFL = "OFL-1.1"  # the SIL Open Font License, which most faces we ship are under
_GUST = "GUST-Font-License"  # Latin Modern's: the LaTeX Project Public License, plus a request
# GNU FreeFont's: a document that embeds it stays the user's, under no licence of ours.
_FREEFONT = "GPL-3.0-or-later WITH Font-exception-2.0"

_ALL_STYLES: tuple[Style, ...] = ("regular", "bold", "italic", "bold-italic")


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
            name=f"{family} {STYLE_NAMES[style]}",
            family=family,
            style=style,
            category=category,
            file=f"{file_stem}-{STYLE_NAMES[style].replace(' ', '')}.ttf",
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
            Cut("bold", "Bold", "FreeSansBold.ttf", same_widths_as=("FreeSansBold",)),
            Cut(
                "italic", "Oblique", "FreeSansOblique.ttf", same_widths_as=("FreeSansOblique",)
            ),
            Cut(
                "bold-italic",
                "Bold Oblique",
                "FreeSansBoldOblique.ttf",
                same_widths_as=("FreeSansBoldOblique",),
            ),
        ),
    ),
    *cut_faces(
        "FreeSerif",
        "serif",
        license=_FREEFONT,
        cuts=(
            Cut("regular", "Regular", "FreeSerif.ttf"),
            Cut("bold", "Bold", "FreeSerifBold.ttf", same_widths_as=("FreeSerifBold",)),
            Cut("italic", "Italic", "FreeSerifItalic.ttf", same_widths_as=("FreeSerifItalic",)),
            Cut(
                "bold-italic",
                "Bold Italic",
                "FreeSerifBoldItalic.ttf",
                same_widths_as=("FreeSerifBoldItalic",),
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
            Cut(
                "regular",
                "Regular",
                "lmroman10-regular.otf",
                same_widths_as=("CMR10", "SFRM1000"),
            ),
            Cut("bold", "Bold", "lmroman10-bold.otf", same_widths_as=("CMBX10", "SFBX1000")),
            Cut(
                "italic",
                "Italic",
                "lmroman10-italic.otf",
                same_widths_as=("CMTI10", "SFTI1000"),
            ),
            Cut(
                "bold-italic",
                "Bold Italic",
                "lmroman10-bolditalic.otf",
                same_widths_as=("CMBXTI10", "SFBI1000"),
            ),
        ),
    ),
    *cut_faces(
        "Latin Modern Roman 12",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman12",),
        cuts=(
            Cut(
                "regular",
                "Regular",
                "lmroman12-regular.otf",
                same_widths_as=("CMR12", "SFRM1200"),
            ),
            Cut("bold", "Bold", "lmroman12-bold.otf", same_widths_as=("CMBX12", "SFBX1200")),
            Cut(
                "italic",
                "Italic",
                "lmroman12-italic.otf",
                same_widths_as=("CMTI12", "SFTI1200"),
            ),
        ),
    ),
    *cut_faces(
        "Latin Modern Roman 17",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman17",),
        cuts=(
            Cut(
                "regular",
                "Regular",
                "lmroman17-regular.otf",
                same_widths_as=("CMR17", "SFRM1728"),
            ),
        ),
    ),
    *cut_faces(
        "Latin Modern Roman Caps 10",
        "serif",
        license=_GUST,
        same_widths_as=("LMRomanCaps10",),
        cuts=(
            Cut(
                "regular",
                "Regular",
                "lmromancaps10-regular.otf",
                same_widths_as=("CMCSC10", "SFCC1000"),
            ),
        ),
    ),
    *cut_faces(
        "Latin Modern Mono 10",
        "mono",
        license=_GUST,
        same_widths_as=("LMMono10",),
        cuts=(
            Cut(
                "regular",
                "Regular",
                "lmmono10-regular.otf",
                same_widths_as=("CMTT10", "SFTT1000"),
            ),
            Cut(
                "italic",
                "Italic",
                "lmmono10-italic.otf",
                same_widths_as=("CMITT10", "SFIT1000"),
            ),
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


@cache
def face_bytes(face: Face) -> bytes:
    """The face's font file, as shipped in the package."""
    return resources.files("squidpdf").joinpath("fonts", face.file).read_bytes()
