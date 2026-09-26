"""The numbers the engine is tuned by. Change one here and it changes everywhere.

Facts about a file format or one algorithm's insides stay next to their code;
these are the judgement calls.
"""

from __future__ import annotations

# The one number the product is judged on: the share of spans that keep their font.
GREEN_RATE_TARGET = 0.8  # below this, substitution is the normal case, not the exception
GREEN_RATE_WARN = 0.5  # below this, the CLI marks a document red rather than yellow

# Runs merge into one span on one baseline, one face, and a gap that's only kerning.
BASELINE_EPS = 0.6
SIZE_EPS = 0.1
GAP_RATIO = 0.35

# Fit. The server's check and the browser's live one both use these.
TOLERANCE_PT = 4.0  # beyond this the line is visibly disturbed
CONDENSE_LIMIT = 0.05  # a squeeze past this reads as condensed, worse than running long
SHRINK_FLOOR = 0.9  # of the original size, set by eye: smaller reads as another line

# Half of `build`: bump it when the substitute fonts change, so browsers refetch.
LIBRARY_VERSION = "1"
