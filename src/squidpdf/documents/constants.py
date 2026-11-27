from __future__ import annotations

_MB = 1024 * 1024

MAX_FILE_MB = 100
MAX_FILE_BYTES = MAX_FILE_MB * _MB
MAX_PAGES = 1_000
# Uploads refused below this, less what each under way may yet write: kept documents write too.
MIN_FREE_BYTES = 2 * 1024 * _MB

MAX_FONT_MB = 25
MAX_FONT_BYTES = MAX_FONT_MB * _MB
MAX_FONTS = 20

ANALYSE_TIMEOUT_S = 30
# As many as an address has at work (`JOBS_AT_WORK`), so no share waits behind another.
ANALYSE_WORKERS = 3
# Each worker opens the document and its fonts again for its share, which fewer don't repay.
SPANS_PER_SHARE = 250
PAGE_IMAGE_TIMEOUT_S = 10

# A small share of WORKER_MEMORY_BYTES once parsed, so the rest is left for a PDF's own work.
KEPT_INDEX_BYTES = 32 * _MB

IDLE_S = 3600
SWEEP_EVERY_S = 60

# A page URL carries `build`, so its bytes never change; kept as long as the document.
PAGE_CACHE = f"private, max-age={IDLE_S}, immutable"
DOCUMENT_CACHE = "private, no-cache"

# The shape of a document's reply: bump it when that changes, so an old copy gets no 304.
REPLY_VERSION = 14
# How an original, its span index and page list are kept: bump it when one changes. One kept
# in another reads as gone, never indexed again, and the browser opens it again from its copy.
DOCUMENT_FORMAT = 5
# How an analysis is kept and judged: bump it when either changes; an old one is redone.
ANALYSIS_FORMAT = 14

MAX_IMAGE_PIXELS = 20_000_000  # a larger page gets a smaller scale instead
