"""What a font's name says: its family, its style and its weight.

A PDF names a font as its maker wrote it, "ABCDEE+Calibri-Bold", "Arial,BoldItalic"
or "Calibri Light Bold", and its description (`FontDescriptor`) may say more. Read
here once, for the look-alike and for Google's copy alike. Pure functions: no PDF
is opened here.
"""

from __future__ import annotations

from dataclasses import dataclass

from squidpdf.core.types import FontDescriptor, Style

# Bits of a PDF font description's /Flags.
_ITALIC = 1 << 6
_FORCE_BOLD = 1 << 18
_BOLD_FROM = 600  # a weight at or past this reads as bold: SemiBold and up
_REGULAR = 400  # the weight a font is when nothing says otherwise
_BOLD = 700  # the weight a plain bold cut is
# The weights fonts come in, lightest to heaviest; a file may name any number.
WEIGHTS = range(100, 1000, 100)

# A style by whether it's (bold, italic).
_STYLE_BY_BOLD_ITALIC: dict[tuple[bool, bool], Style] = {
    (False, False): "regular",
    (True, False): "bold",
    (False, True): "italic",
    (True, True): "bold-italic",
}


@dataclass(frozen=True, slots=True)
class _WeightWord:
    """A weight word in a font's name, and the weight it means."""

    word: str  # lower case, as the name reads with its spaces dropped
    weight: int  # 100 to 900


# Each weight word; the first match wins, so compound words come first: "SemiBold"
# isn't read as "Bold", nor "SemiLight" as "Light". IBM Plex's names shorten some:
# "IBMPlexMono-ExtLt", "-Medm", "-SmBld".
_WEIGHT_WORDS = (
    _WeightWord("extralight", 200),
    _WeightWord("ultralight", 200),
    _WeightWord("extlt", 200),
    _WeightWord("semilight", 350),
    _WeightWord("demilight", 350),
    _WeightWord("semibold", 600),
    _WeightWord("demibold", 600),
    _WeightWord("smbld", 600),
    _WeightWord("extrabold", 800),
    _WeightWord("ultrabold", 800),
    _WeightWord("hairline", 100),
    _WeightWord("thin", 100),
    _WeightWord("light", 300),
    _WeightWord("medium", 500),
    _WeightWord("medm", 500),
    _WeightWord("bold", 700),
    _WeightWord("black", 900),
    _WeightWord("heavy", 900),
    # Between two of the nine, as IBM Plex's Text cuts and Fira Code's Retina are.
    _WeightWord("retina", 450),
    _WeightWord("text", 450),
)

# Words in a font's name after the family, and what they say about its style. Bold:
# a weight word of SemiBold or more, wherever it is.
_BOLD_WORDS = tuple(each.word for each in _WEIGHT_WORDS if each.weight >= _BOLD_FROM)
_ITALIC_WORDS = ("italic", "oblique", "ital")
# Another weight or width than regular or bold: the letters are wider or narrower.
# Every weight word but bold's, and the parts of the names of other widths.
_OTHER_CUT_WORDS = (
    *(each.word for each in _WEIGHT_WORDS if each.weight != _BOLD),
    "medi",
    "semi",
    "demi",
    "extra",
    "ultra",
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


def _family_and_style(font: str) -> tuple[str, str]:
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
    family, _style = _family_and_style(font)
    return family.replace(" ", "").lower()


def style_of(font: str, descriptor: FontDescriptor | None) -> tuple[Style, bool]:
    """The style a font's name and description say it is, and whether it's a usual cut.

    Bold when its weight (`weight_of`) is, when its name says bold anywhere,
    or when the file has the viewer draw it bold (ForceBold). A usual cut is
    plain regular, bold or italic: a light or narrow cut of a family we know
    still has other letter widths than the face we ship.
    """
    _family_name, style_words = _family_and_style(font)
    style_text = style_words.lower()
    # "Calibri Light Bold" or "Calibri-Light,Bold": a light font drawn bold.
    named_bold = any(word in style_text for word in _BOLD_WORDS)
    forced_bold = descriptor is not None and bool(descriptor.flags & _FORCE_BOLD)
    heavy = weight_of(font, descriptor) >= _BOLD_FROM
    bold = heavy or named_bold or forced_bold
    named_italic = any(word in style_text for word in _ITALIC_WORDS)
    slanted = descriptor is not None and (
        bool(descriptor.flags & _ITALIC) or descriptor.italic_angle != 0
    )
    italic = named_italic or slanted
    usual_cut = not any(word in style_text for word in _OTHER_CUT_WORDS)
    return _STYLE_BY_BOLD_ITALIC[(bold, italic)], usual_cut


def weight_of(font: str, descriptor: FontDescriptor | None) -> int:
    """A font's own weight: from its name, else its description, else regular.

    It may be none of the nine (`WEIGHTS`): between two, as SemiLight's 350 or
    a Text cut's 450 is, or past either end, as a description's 0 is, since
    the file may write any number there.
    """
    _family_name, style_words = _family_and_style(font)
    style_text = style_words.replace(" ", "").lower()
    named = next((each.weight for each in _WEIGHT_WORDS if each.word in style_text), None)
    # The name says it: "Poppins-SemiBold".
    if named is not None:
        return named
    # Nothing says it.
    if descriptor is None or descriptor.weight is None:
        return _REGULAR
    return round(descriptor.weight)
