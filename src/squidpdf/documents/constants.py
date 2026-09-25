"""How long a document lives, how often the dead ones go, and how browsers keep it."""

from __future__ import annotations

IDLE_S = 3600  # a document untouched this long is deleted
SWEEP_EVERY_S = 60

# A page URL carries `build`, so its bytes never change; kept as long as the document.
PAGE_CACHE = f"private, max-age={IDLE_S}, immutable"
DOCUMENT_CACHE = "private, no-cache"  # always asked again, answered 304 if unchanged
