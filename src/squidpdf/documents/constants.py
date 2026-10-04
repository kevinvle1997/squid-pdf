"""What an upload may be, how long a document lives, and how its page images are kept."""

from __future__ import annotations

_MB = 1024 * 1024

# Uploads.
MAX_FILE_MB = 100  # as the refusal says it
MAX_FILE_BYTES = MAX_FILE_MB * _MB
MAX_PAGES = 1_000
# Uploads refused below this, less a whole file per upload under way: kept documents write too.
MIN_FREE_BYTES = 2 * 1024 * _MB

# Workers. Past a timeout the task is killed and the user told it took too long.
ANALYSE_TIMEOUT_S = 30  # the analysis, at upload or under a new build
PAGE_IMAGE_TIMEOUT_S = 10

IDLE_S = 3600  # a document untouched this long is deleted
SWEEP_EVERY_S = 60

# A page URL carries `build`, so its bytes never change; kept as long as the document.
PAGE_CACHE = f"private, max-age={IDLE_S}, immutable"
DOCUMENT_CACHE = "private, no-cache"  # always asked again, answered 304 if unchanged

MAX_IMAGE_PIXELS = 20_000_000  # a larger page gets a smaller scale instead
