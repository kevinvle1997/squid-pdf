"""Which font can draw this, and what to use when the document's own cannot.

A PDF usually embeds only the glyphs the document actually used, so whether an
edit is possible in the original face depends on what the user types. That is the
question this module answers.
"""

from __future__ import annotations

# Metric-compatible stand-ins: identical letter widths, so a substitution shifts
# nothing on the page and only the letterforms differ. All OFL or Apache, all
# free to embed. Keyed on the bare family name.
SUBSTITUTES: dict[str, str] = {
    "arial": "Liberation Sans",
    "helvetica": "Liberation Sans",
    "arialnarrow": "Liberation Sans Narrow",
    "timesnewroman": "Liberation Serif",
    "times": "Liberation Serif",
    "timesroman": "Liberation Serif",
    "couriernew": "Liberation Mono",
    "courier": "Liberation Mono",
    "calibri": "Carlito",
    "cambria": "Caladea",
}

# The floor. Reached when nothing above matches, or when the replacement needs a
# character no Latin face carries. An edit should degrade visibly, never fail.
FALLBACK = "Noto Sans"

# None of SUBSTITUTES is bundled yet (see HANDOFF gap #1), so drawing still needs
# a real font today. These three PDF base-14 names ship with every reader and
# need no file of ours, and are historically metric-compatible with the family
# they stand in for. Calibri and Cambria have no base-14 equivalent and fall
# through to Helvetica until Carlito/Caladea are actually bundled.
_BASE14 = {
    "arial": "helv",
    "helvetica": "helv",
    "arialnarrow": "helv",
    "timesnewroman": "tiro",
    "times": "tiro",
    "timesroman": "tiro",
    "couriernew": "cour",
    "courier": "cour",
}
_BASE14_FALLBACK = "helv"

# What each of those is called, for telling the user which face drew their edit.
_BASE14_NAMES = {"helv": "Helvetica", "tiro": "Times", "cour": "Courier"}

# The faces new text can always be drawn in, by the name the user picks.
BUILT_IN = tuple(_BASE14_NAMES.values())


def strip_subset(font: str) -> str:
    """`ABCDEE+Calibri-Bold` -> `Calibri-Bold`. Style is kept, only the prefix goes."""
    return font.split("+", 1)[-1]


def bare_name(font: str) -> str:
    """`ABCDEE+Calibri-Bold` -> `calibri`.

    Subset prefixes and style suffixes are noise when looking up a *family*.
    Note this deliberately reads the name; the classifier that handles unmapped
    fonts must not, because names lie: `NimbusRomNo9L` is a serif despite
    containing no "roman". Do not use this to identify one font resource on a
    page, where two different weights share a bare name; use `strip_subset` there.
    """
    family = strip_subset(font).split("-", 1)[0]  # Calibri-Bold -> Calibri
    family = family.split(",", 1)[0]  # Arial,Bold -> Arial
    return family.replace(" ", "").lower()


def substitute_for(font: str) -> str:
    """The closest face that will not move anything on the page, once it's bundled.

    Not what draws today: tell the user `drawn_in`'s answer until it is.
    """
    return SUBSTITUTES.get(bare_name(font), FALLBACK)


def base14_for(font: str) -> str:
    """The built-in PDF font actually used to draw a substitute, today.

    Only a stand-in for `substitute_for`'s answer until the real files in
    SUBSTITUTES are bundled (see HANDOFF gap #1).
    """
    return _BASE14.get(bare_name(font), _BASE14_FALLBACK)


def drawn_in(font: str) -> str:
    """The name of the face that really draws an edit in this font today, e.g. "Helvetica".

    The one to tell the user: naming a look-alike we don't ship would promise
    a width we can't keep.
    """
    return _BASE14_NAMES[base14_for(font)]
