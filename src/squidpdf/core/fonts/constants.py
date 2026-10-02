"""What the font code knows about styles and kinds of font, as tables.

The faces themselves are `catalog.py`'s; the numbers the engine is tuned by are
`core/constants.py`'s.
"""

from __future__ import annotations

from squidpdf.core.types import Category, Style

# How each style is written in a face's name; its file's name drops the spaces.
STYLE_NAMES: dict[Style, str] = {
    "regular": "Regular",
    "bold": "Bold",
    "italic": "Italic",
    "bold-italic": "Bold Italic",
}

# A style by whether it's (bold, italic).
STYLE_BY_BOLD_ITALIC: dict[tuple[bool, bool], Style] = {
    (False, False): "regular",
    (True, False): "bold",
    (False, True): "italic",
    (True, True): "bold-italic",
}

# A style a family lacks falls back one step at a time.
NEAREST_STYLE: dict[Style, Style] = {
    "bold-italic": "bold",
    "italic": "regular",
    "bold": "regular",
}

# The family that draws a letter a look-alike lacks, by the look-alike's kind of font.
BROADEST_FAMILY: dict[Category, str] = {
    "sans": "Noto Sans",
    "serif": "Noto Serif",
    "mono": "Noto Sans",
    "handwriting": "Noto Sans",
}

# A document font we know nothing about gets a family of its kind; letter widths will differ.
PLAIN_FAMILY: dict[Category, str] = {
    "sans": "Liberation Sans",
    "serif": "Liberation Serif",
    "mono": "Liberation Mono",
}
