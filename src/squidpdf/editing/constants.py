"""What editing accepts, how long its work may take, and how browsers keep what it sends.

Limits are starting values, not findings (decisions/api.md, Limits). They
protect the server, not the business, so no plan or account lifts them.
"""

from __future__ import annotations

# Render and export requests.
MAX_EDITS = 10_000
MAX_TEXT_CHARS = 1_000  # typed in one edit: a replacement or an insert

# Workers. Past a timeout the task is killed and the user told it took too long.
RENDER_TIMEOUT_S = 10

# The font list's URL carries `build`, so its bytes never change, and it's the same for
# everyone: any cache may keep it for good.
FONT_LIST_CACHE = "public, max-age=31536000, immutable"
