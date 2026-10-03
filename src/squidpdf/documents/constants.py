"""What an upload may be, how long a document lives, how its pages and files are kept."""

from __future__ import annotations

_MB = 1024 * 1024

# Uploads.
MAX_FILE_MB = 100  # as the refusal says it
MAX_FILE_BYTES = MAX_FILE_MB * _MB
MAX_PAGES = 1_000
# Uploads refused below this, less a whole file per upload under way: kept documents write too.
MIN_FREE_BYTES = 2 * 1024 * _MB

# Workers. Past a timeout the task is killed and the user told it took too long.
ANALYSE_TIMEOUT_S = 30  # the analysis, at upload, or under a new build, tuning or format
PAGE_IMAGE_TIMEOUT_S = 10

IDLE_S = 3600  # a document untouched this long is deleted
SWEEP_EVERY_S = 60

# A page URL carries `build`, so its bytes never change; kept as long as the document.
PAGE_CACHE = f"private, max-age={IDLE_S}, immutable"
DOCUMENT_CACHE = "private, no-cache"  # always asked again, answered 304 if unchanged

# Versions: bump one when a shape changes.
# The shape of a document's reply (`document_json`): bump it when what the reply sends
# changes, so a browser holding the old shape is never answered 304.
REPLY_VERSION = 1
# The format a document's span index and page list are kept in: bump it when a saved span
# or page changes shape. A document kept in another reads as gone, and the browser opens
# it again from its own copy: an index is built once, at upload, never over a kept one.
DOCUMENT_FORMAT = 1
# The format an analysis is kept in: bump it when what it keeps changes shape. One kept
# in another is worked out again over the same index, so every span id holds.
ANALYSIS_FORMAT = 1

MAX_IMAGE_PIXELS = 20_000_000  # a larger page gets a smaller scale instead
