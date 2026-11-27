from __future__ import annotations

from dataclasses import dataclass

from squidpdf.core.types import FontDescriptor, Style

_ITALIC = 1 << 6
_FORCE_BOLD = 1 << 18
_BOLD_FROM = 600
_REGULAR = 400
_BOLD = 700
WEIGHTS = range(100, 1000, 100)

_STYLE_BY_BOLD_ITALIC: dict[tuple[bool, bool], Style] = {
    (False, False): "regular",
    (True, False): "bold",
    (False, True): "italic",
    (True, True): "bold-italic",
}


@dataclass(frozen=True, slots=True)
class _WeightWord:
    word: str
    weight: int


# The first match wins, so compounds go first.
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
    _WeightWord("retina", 450),
    _WeightWord("text", 450),
)

_BOLD_WORDS = tuple(each.word for each in _WEIGHT_WORDS if each.weight >= _BOLD_FROM)
_ITALIC_WORDS = ("italic", "oblique", "ital")
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
    return font.split("+", 1)[-1]


def _family_and_style(font: str) -> tuple[str, str]:
    name = strip_subset(font)
    for mark in ("-", ","):
        if mark in name:
            family, style = name.split(mark, 1)
            return family, style
    words = name.split()
    style_words: list[str] = []
    while len(words) > 1 and words[-1].lower() in _STYLE_WORDS:
        style_words.insert(0, words.pop())
    return " ".join(words), " ".join(style_words)


def bare_name(font: str) -> str:
    """Never guess an unknown font's kind from it: names lie."""
    family, _style = _family_and_style(font)
    return family.replace(" ", "").lower()


def style_of(font: str, descriptor: FontDescriptor | None) -> tuple[Style, bool]:
    """A light or narrow cut has other letter widths than the face we ship."""
    _family_name, style_words = _family_and_style(font)
    style_text = style_words.lower()
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
    _family_name, style_words = _family_and_style(font)
    style_text = style_words.replace(" ", "").lower()
    named = next((each.weight for each in _WEIGHT_WORDS if each.word in style_text), None)
    if named is not None:
        return named
    if descriptor is None or descriptor.weight is None:
        return _REGULAR
    return round(descriptor.weight)
