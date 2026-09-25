"""The numbers the engine is tuned by. Change one here and it changes everywhere.

Facts about a file format or one algorithm's insides stay next to their code;
these are the judgement calls.
"""

from __future__ import annotations

# The one number the product is judged on: the share of spans that keep their font.
GREEN_RATE_TARGET = 0.8  # below this, substitution is the normal case, not the exception
GREEN_RATE_WARN = 0.5  # below this, the CLI marks a document red rather than yellow

# Two runs belong to the same span when they sit on one baseline, share a face,
# and are close enough that the gap is kerning rather than a layout decision.
BASELINE_EPS = 0.6
SIZE_EPS = 0.1
GAP_RATIO = 0.35

# Fit. The server's check and the browser's live one both use these.
TOLERANCE_PT = 4.0  # beyond this the line is visibly disturbed
# A horizontal squeeze up to here is invisible; past it the text reads as
# condensed, which is worse than a slightly long line.
CONDENSE_LIMIT = 0.05
