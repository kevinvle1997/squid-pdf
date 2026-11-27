from __future__ import annotations

from dataclasses import dataclass

GREEN_RATE_TARGET = 0.8  # below this, substitution is the normal case, not the exception
GREEN_RATE_WARN = 0.5  # below this, the CLI marks a document red rather than yellow

BASELINE_EPS = 0.6  # pt: two letters' baselines this close are one line
SIZE_EPS = 0.1  # pt: two runs' sizes this close are one size
GAP_RATIO = 0.35  # of the size: a gap past this between two runs is a word's, not kerning
KERNING_PT = -1.0  # a gap down to this far below 0 is kerning pulling letters together

# Per 1000 em: a width list rounds to whole units.
SAME_WIDTH = 1.0
# A few letters can agree by chance.
SAME_FONT_SHARED = 3

# A span's box is kept to hundredths of a point: a letter on its edge can sit this far past it.
ROUNDED_PT = 0.005
# Past the rounding of a rewritten line, well short of anything a reader sees.
MOVED_PT = 0.05

# The server's fit check and the browser's live one both use these.
TOLERANCE_PT = 4.0  # beyond this the line is visibly disturbed
# Less than this, the warning would say 0.0 and nobody could see it.
ROOM_SLACK_PT = 0.1

# A kerned pair moves about 0.01 em, a tight heading 0.02, a tracked label 0.05.
LETTER_SPACING_MIN_EM = 0.015

# A stroke's ink spreads a little past where it's drawn.
LINE_EDGE_SLACK_PT = 1.0

ROOM_TOUCH_PT = 0.1  # boxes overlapping by less than this don't touch: a hairline is no wall
# Searched apart, so a frame doesn't widen the search through a chart's many short strokes.
ROOM_TALL_ART_LINES = 4.0

# A trimmed font's kind, read from its letters where its file and PDF state none. A sans's
# stems end as wide as they run; an upright serif's feet are twice the stem or more.
SERIF_FOOT_SPREAD = 1.3
# An italic serif's capitals and half its lowercase have feet; a sans's one letter at most.
SERIF_SHARE = 0.4
SERIF_LETTERS_MIN = 2
# Two letters can share an advance by chance.
MONO_LETTERS_MIN = 3

# Sine of the angle, about half a degree: redraws are level, and that drifts 4 pt over a page.
TURN_TOLERANCE = 0.01
CONDENSE_LIMIT = 0.05  # a squeeze past this reads as condensed, worse than running long
SHRINK_FLOOR = 0.9  # of the original size, set by eye: smaller reads as another line

# MuPDF's garbage level: 3 also merges copies, in time square in the pages.
GARBAGE_COLLECT = 2

# zlib-ng's level: grey text pages at 3 are a little larger than at 6, in far less time.
PNG_GRAY_LEVEL = 3
PNG_COLOR_LEVEL = 6  # the usual level, MuPDF's own: lower made some photos larger

# Bump it when what's drawn or judged changes, so nothing older is reused.
LIBRARY_VERSION = "11"

# Part of `build`: a new pin judges every document again.
GOOGLE_FONTS_COMMIT = "23e54b51ddffbc7713c583748e3bd86f62b1fa4a"
FETCH_TIMEOUT_S = 5.0  # past this, the line goes to the substitute
# A fetch with no answer holds back every copy this long: each would wait out the timeout.
FETCH_RETRY_S = 600.0
KEYBOARD_RANGES = (range(0x20, 0x7F), range(0xA0, 0x100))  # a pool missing one asks Google
# Each is a few tens of KB, so all of them stay near a megabyte.
LENT_COPIES_KEPT = 32

# An analysis is kept under these numbers, so add any new one here.
ANALYSIS_TUNING = {
    "baseline_eps": BASELINE_EPS,
    "gap_ratio": GAP_RATIO,
    "kerning_pt": KERNING_PT,
    "line_edge_slack_pt": LINE_EDGE_SLACK_PT,
    "tolerance_pt": TOLERANCE_PT,
    "room_touch_pt": ROOM_TOUCH_PT,
    "letter_spacing_min_em": LETTER_SPACING_MIN_EM,
    "turn_tolerance": TURN_TOLERANCE,
    "same_width": SAME_WIDTH,
    "same_font_shared": SAME_FONT_SHARED,
    "keyboard_ranges": KEYBOARD_RANGES,
}

# Short to keep the font list small; letters past these still draw.
GLYPH_LIST_RANGES = (
    range(0x20, 0x250),
    range(0x370, 0x530),
    range(0x1E00, 0x1F00),
    range(0x2000, 0x2200),
)


@dataclass(frozen=True, slots=True)
class _OptionKeys:
    """Defined here, not in core/types.py, since that module reads this one."""

    label: str
    detail: str


OPTION_KEYS = {
    "shrink": _OptionKeys(label="shrink_label", detail="shrink_detail"),
    "condense": _OptionKeys(label="condense_label", detail="condense_detail"),
    "as-is": _OptionKeys(label="as_is_label", detail="as_is_detail"),
}

# Another reader may read a line end after ID as one byte, or decode fewer bytes ahead.
IMAGE_SOONER_BY = 16
# Past this, a decompression bomb would fill a worker's memory, so it's cut there.
DECODED_PER_BYTE = 8
