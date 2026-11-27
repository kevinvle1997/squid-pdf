from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

from squidpdf.core.types import Category, Face, Style

_OFL = "OFL-1.1"
_GUST = "GUST-Font-License"
# GNU FreeFont's: a document that embeds it stays the user's, under no licence of ours.
_FREEFONT = "GPL-3.0-or-later WITH Font-exception-2.0"

_ALL_STYLES: tuple[Style, ...] = ("regular", "bold", "italic", "bold-italic")

_STYLE_NAMES: dict[Style, str] = {
    "regular": "Regular",
    "bold": "Bold",
    "italic": "Italic",
    "bold-italic": "Bold Italic",
}


def _family_faces(
    family: str,
    category: Category,
    *,
    same_widths_as: tuple[str, ...] = (),
    styles: tuple[Style, ...] = _ALL_STYLES,
) -> tuple[Face, ...]:
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
class _Cut:
    style: Style
    called: str
    file: str
    same_widths_as: tuple[str, ...] = ()


def _cut_faces(
    family: str,
    category: Category,
    *,
    license: str,
    cuts: tuple[_Cut, ...],
    same_widths_as: tuple[str, ...] = (),
) -> tuple[Face, ...]:
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


CATALOG: tuple[Face, ...] = (
    *_family_faces(
        "Liberation Sans",
        "sans",
        same_widths_as=("Arial", "ArialMT", "Helvetica", "Arimo", "Nimbus Sans", "NimbusSanL"),
    ),
    *_family_faces(
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
    *_family_faces(
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
    *_family_faces("Carlito", "sans", same_widths_as=("Calibri",)),
    *_family_faces("Caladea", "serif", same_widths_as=("Cambria",)),
    *_family_faces("Noto Sans", "sans"),
    *_family_faces("Noto Serif", "serif"),
    *_family_faces("Poppins", "sans"),
    *_family_faces("Open Sans", "sans"),
    *_family_faces("Montserrat", "sans"),
    *_family_faces("Nunito", "sans"),
    *_family_faces("PT Sans", "sans"),
    *_family_faces("PT Serif", "serif"),
    *_cut_faces(
        "FreeSans",
        "sans",
        license=_FREEFONT,
        cuts=(
            _Cut("regular", "Regular", "FreeSans.ttf"),
            _Cut("bold", "Bold", "FreeSansBold.ttf", same_widths_as=("FreeSansBold",)),
            _Cut(
                "italic", "Oblique", "FreeSansOblique.ttf", same_widths_as=("FreeSansOblique",)
            ),
            _Cut(
                "bold-italic",
                "Bold Oblique",
                "FreeSansBoldOblique.ttf",
                same_widths_as=("FreeSansBoldOblique",),
            ),
        ),
    ),
    *_cut_faces(
        "FreeSerif",
        "serif",
        license=_FREEFONT,
        cuts=(
            _Cut("regular", "Regular", "FreeSerif.ttf"),
            _Cut("bold", "Bold", "FreeSerifBold.ttf", same_widths_as=("FreeSerifBold",)),
            _Cut(
                "italic", "Italic", "FreeSerifItalic.ttf", same_widths_as=("FreeSerifItalic",)
            ),
            _Cut(
                "bold-italic",
                "Bold Italic",
                "FreeSerifBoldItalic.ttf",
                same_widths_as=("FreeSerifBoldItalic",),
            ),
        ),
    ),
    # TeX names a font by cut and size (CMBX10: bold, 10 pt design), so each name pins one face.
    *_cut_faces(
        "Latin Modern Roman 10",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman10",),
        cuts=(
            _Cut(
                "regular",
                "Regular",
                "lmroman10-regular.otf",
                same_widths_as=("CMR10", "SFRM1000"),
            ),
            _Cut("bold", "Bold", "lmroman10-bold.otf", same_widths_as=("CMBX10", "SFBX1000")),
            _Cut(
                "italic",
                "Italic",
                "lmroman10-italic.otf",
                same_widths_as=("CMTI10", "SFTI1000"),
            ),
            _Cut(
                "bold-italic",
                "Bold Italic",
                "lmroman10-bolditalic.otf",
                same_widths_as=("CMBXTI10", "SFBI1000"),
            ),
        ),
    ),
    *_cut_faces(
        "Latin Modern Roman 12",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman12",),
        cuts=(
            _Cut(
                "regular",
                "Regular",
                "lmroman12-regular.otf",
                same_widths_as=("CMR12", "SFRM1200"),
            ),
            _Cut("bold", "Bold", "lmroman12-bold.otf", same_widths_as=("CMBX12", "SFBX1200")),
            _Cut(
                "italic",
                "Italic",
                "lmroman12-italic.otf",
                same_widths_as=("CMTI12", "SFTI1200"),
            ),
        ),
    ),
    *_cut_faces(
        "Latin Modern Roman 17",
        "serif",
        license=_GUST,
        same_widths_as=("LMRoman17",),
        cuts=(
            _Cut(
                "regular",
                "Regular",
                "lmroman17-regular.otf",
                same_widths_as=("CMR17", "SFRM1728"),
            ),
        ),
    ),
    *_cut_faces(
        "Latin Modern Roman Caps 10",
        "serif",
        license=_GUST,
        same_widths_as=("LMRomanCaps10",),
        cuts=(
            _Cut(
                "regular",
                "Regular",
                "lmromancaps10-regular.otf",
                same_widths_as=("CMCSC10", "SFCC1000"),
            ),
        ),
    ),
    *_cut_faces(
        "Latin Modern Mono 10",
        "mono",
        license=_GUST,
        same_widths_as=("LMMono10",),
        cuts=(
            _Cut(
                "regular",
                "Regular",
                "lmmono10-regular.otf",
                same_widths_as=("CMTT10", "SFTT1000"),
            ),
            _Cut(
                "italic",
                "Italic",
                "lmmono10-italic.otf",
                same_widths_as=("CMITT10", "SFIT1000"),
            ),
        ),
    ),
    *_family_faces("Inter", "sans"),
    *_family_faces("Roboto", "sans"),
    *_family_faces("Lato", "sans"),
    *_family_faces("EB Garamond", "serif"),
    *_family_faces("IBM Plex Serif", "serif"),
    *_family_faces("IBM Plex Mono", "mono"),
    *_family_faces("Caveat", "handwriting", styles=("regular", "bold")),
    *_family_faces("Great Vibes", "handwriting", styles=("regular",)),
)

FACES: dict[str, Face] = {face.name: face for face in CATALOG}


@cache
def face_bytes(face: Face) -> bytes:
    return face_bytes_anew(face)


def face_bytes_anew(face: Face) -> bytes:
    return resources.files("squidpdf").joinpath("fonts", face.file).read_bytes()
