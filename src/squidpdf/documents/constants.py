"""What an upload may be, how long a document lives, how its pages and files are kept."""

from __future__ import annotations

_MB = 1024 * 1024

# Uploads.
MAX_FILE_MB = 100  # as the refusal says it
MAX_FILE_BYTES = MAX_FILE_MB * _MB
MAX_PAGES = 1_000
# Uploads refused below this, less what each under way may yet write: kept documents write too.
MIN_FREE_BYTES = 2 * 1024 * _MB

# The user's own copies of the document's fonts: starting values.
MAX_FONT_MB = 25  # as the refusal says it
MAX_FONT_BYTES = MAX_FONT_MB * _MB
MAX_FONTS = 20  # the user's own copies kept per document

# Workers. Past a timeout the task is killed and the user told it took too long.
ANALYSE_TIMEOUT_S = 30  # the analysis, at upload or again after a deploy
PAGE_IMAGE_TIMEOUT_S = 10

# The indexes a worker keeps read, by their files' bytes: a few long documents', a small share
# of WORKER_MEMORY_BYTES once parsed, so the rest is left for a PDF's own work.
KEPT_INDEX_BYTES = 32 * _MB

IDLE_S = 3600  # a document untouched this long is deleted
SWEEP_EVERY_S = 60

# A page URL carries `build`, so its bytes never change; kept as long as the document.
PAGE_CACHE = f"private, max-age={IDLE_S}, immutable"
DOCUMENT_CACHE = "private, no-cache"  # always asked again, answered 304 if unchanged

# The shape of a document's reply: bump it when that changes, so an old copy gets no 304.
REPLY_VERSION = 8
# How an original, its span index and page list are kept: bump it when one changes. One kept
# in another reads as gone, never indexed again, and the browser opens it again from its copy.
DOCUMENT_FORMAT = 3
# How an analysis is kept and judged: bump it when either changes; an old one is redone.
ANALYSIS_FORMAT = 8

MAX_IMAGE_PIXELS = 20_000_000  # a larger page gets a smaller scale instead
