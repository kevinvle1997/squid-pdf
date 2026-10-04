"""Editing's limits and timeouts, and how browsers keep what it sends.

Limits protect the server, so no plan or account lifts them.
"""

from __future__ import annotations

# Render and export requests.
MAX_EDITS = 10_000
MAX_TEXT_CHARS = 1_000  # typed in one edit: a replacement or an insert
# Regions in a render: the browser asks for one per group of edited rows, so at most its edits.
MAX_REGIONS = MAX_EDITS
# A page's strips, out to whole pixel rows, total at most this many times its image's rows.
# The browser's (strips.ts) share no rows and are padded taller than one, so no row is in three.
MAX_STRIP_ROWS_PER_PAGE_ROW = 2

# Render redraws only the edits whose rows it shows. A redraw can reach this far past
# its span's box, in its size: an accent above a capital, a substitute's longer tail.
REDRAW_REACH_EM = 0.5

# Workers. Past a timeout the task is killed and the user told it took too long.
RENDER_TIMEOUT_S = 10
EXPORT_TIMEOUT_S = 60
FONT_LIST_TIMEOUT_S = 30  # a few seconds of pool work, once per server: room for a slow machine

# The font list's URL carries `build`, so its bytes never change, and it's the same for
# everyone: any cache may keep it for good.
FONT_LIST_CACHE = "public, max-age=31536000, immutable"
