"""The numbers the engine is tuned by. Change one here and it changes everywhere.

Facts about a file format or one algorithm's insides stay next to their code;
these are the judgement calls.
"""

from __future__ import annotations

from dataclasses import dataclass

# The one number the product is judged on: the share of spans that keep their font.
GREEN_RATE_TARGET = 0.8  # below this, substitution is the normal case, not the exception
GREEN_RATE_WARN = 0.5  # below this, the CLI marks a document red rather than yellow

# Runs merge into one span on one baseline, one face, and a gap that's only kerning.
BASELINE_EPS = 0.6
SIZE_EPS = 0.1
GAP_RATIO = 0.35

# Two copies of a font under one name are one font when every letter both draw
# is this close in width, per 1000 em: a width list rounds to whole units.
SAME_WIDTH = 1.0
# ...and only when they share at least this many letters: a few can agree by chance.
SAME_FONT_SHARED = 3

# Fit. The server's check and the browser's live one both use these.
TOLERANCE_PT = 4.0  # beyond this the line is visibly disturbed

# A line turned further than this (the sine of its angle, about half a degree) isn't
# redrawn as it was: redraws are level, and half a degree drifts 4 pt over a page's width.
TURN_TOLERANCE = 0.01
CONDENSE_LIMIT = 0.05  # a squeeze past this reads as condensed, worse than running long
SHRINK_FLOOR = 0.9  # of the original size, set by eye: smaller reads as another line

# How hard a save cleans the file: MuPDF's garbage level. 2 drops unused objects;
# 3 also merges copies, which takes time in the square of the pages.
GARBAGE_COLLECT = 2

# Half of `build`: bump it when the fonts we ship change, so browsers refetch.
# 2: the real look-alike files (Liberation, Carlito, Caladea, Noto), not base-14.
# 3: a space a face doesn't map no longer counts as drawable, so letter lists changed.
# 4: a font with no space of its own gives a space the gap the file drew, so widths changed.
# 5: a letter another copy of the same font in the file draws counts, so letter lists changed.
# 6: more families (Poppins, Open Sans, FreeSans, Latin Modern...), so more look-alikes changed.
# 7: text turned, or spaced unlike its font's own widths, is approximate, not exact.
# 8: Google's copy of a font lends the letters the file's copies lack, so fidelity changed.
# 9: text upside down is turned, so it's approximate, not exact.
LIBRARY_VERSION = "9"

# Google's font collection (github.com/google/fonts) at this commit: the family
# list in `fonts/google-families.json` is read from it, and every copy is fetched
# from it. Part of `build`: a new pin judges every document again.
GOOGLE_FONTS_COMMIT = "23e54b51ddffbc7713c583748e3bd86f62b1fa4a"
# How long one fetch of Google's copy may take before the line goes to the substitute.
FETCH_TIMEOUT_S = 5.0
# How long a copy that failed to come is left before it's tried again; a fetch with
# no answer leaves every copy that long, since each would wait out the timeout too.
FETCH_RETRY_S = 600.0
# The letters a Western keyboard types: a pool missing one looks to Google's copy.
KEYBOARD_RANGES = (range(0x20, 0x7F), range(0xA0, 0x100))

# The letters a face we ship lists widths for, so the browser can preview new text:
# Latin, Greek, Cyrillic, punctuation, currency, letterlike signs and arrows.
# Short to keep the font list small; letters past these still draw.
GLYPH_LIST_RANGES = (
    range(0x20, 0x250),
    range(0x370, 0x530),
    range(0x1E00, 0x1F00),
    range(0x2000, 0x2200),
)


@dataclass(frozen=True, slots=True)
class _OptionKeys:
    """One way out of a too-long edit: the keys of the sentences that offer it.

    Defined here, not in core/types.py, since that module reads this one.
    """

    label: str  # what the choice is called, e.g. "Make it slightly smaller"
    detail: str  # what it does to the line; some take how far the text runs over


# The ways out of a too-long edit, by the name an edit's `strategy` uses: what offers
# each. The fit check and the browser's copy of its sentences both read it.
OPTION_KEYS = {
    "shrink": _OptionKeys(label="shrink_label", detail="shrink_detail"),
    "condense": _OptionKeys(label="condense_label", detail="condense_detail"),
    "as-is": _OptionKeys(label="as_is_label", detail="as_is_detail"),
}
