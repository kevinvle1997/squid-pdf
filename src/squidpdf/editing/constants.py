"""Limits protect the server, so no plan or account lifts them."""

from __future__ import annotations

MAX_EDITS = 10_000
MAX_TEXT_CHARS = 1_000
# Regions in a render: the browser asks for one per group of edited rows, so at most its edits.
MAX_REGIONS = MAX_EDITS
# Whole-row strips add up to at most this times the image; the browser's share no rows.
MAX_STRIP_ROWS_PER_PAGE_ROW = 2
# With the rows above, it bounds the images, which cross from the worker in one answer.
MAX_RENDER_PAGES = 8

# A redraw can reach this far past its box: an accent above a capital, a substitute's tail.
REDRAW_REACH_EM = 0.5

# Workers. Past a timeout the task is killed and the user told it took too long.
RENDER_TIMEOUT_S = 10
EXPORT_TIMEOUT_S = 60
FONT_LIST_TIMEOUT_S = 30  # a few seconds of pool work, once per server: room for a slow machine

# The URL carries `build`, so its bytes never change: any cache may keep it for good.
FONT_LIST_CACHE = "public, max-age=31536000, immutable"
