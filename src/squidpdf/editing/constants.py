"""How browsers keep what editing sends them."""

from __future__ import annotations

# The font list's URL carries `build`, so its bytes never change, and it's the same for
# everyone: any cache may keep it for good.
FONT_LIST_CACHE = "public, max-age=31536000, immutable"
