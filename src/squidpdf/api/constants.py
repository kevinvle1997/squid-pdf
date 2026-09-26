"""What the server will accept and how hard it works. Starting values, not findings.

These protect the server, not the business, so no plan or account lifts them.
"""

from __future__ import annotations

_MB = 1024 * 1024

# Page images.
PAGE_SCALES = (1, 2, 3, 4)
MAX_IMAGE_PIXELS = 20_000_000  # a larger page gets a smaller scale instead

# Uploads.
MAX_FILE_MB = 100  # as the refusal says it
MAX_FILE_BYTES = MAX_FILE_MB * _MB
MAX_PAGES = 1_000
UPLOADS_PER_MINUTE = 20  # per IP

# Render and export requests.
MAX_EDITS = 10_000
MAX_REPLACE_CHARS = 1_000
MAX_BODY_BYTES = 5 * _MB

# Attached fonts.
MAX_FONT_BYTES = 25 * _MB
MAX_FONTS = 20  # per document

# Workers. Past a timeout the task is killed and the user told it took too long.
UPLOAD_TIMEOUT_S = 30
RENDER_TIMEOUT_S = 10
EXPORT_TIMEOUT_S = 60
FONT_LIST_TIMEOUT_S = 30  # measured at 5 s; once per server, so room for a slow machine
WORKER_MEMORY_BYTES = 1024 * _MB
TASKS_PER_WORKER = 100  # then replaced, so leaked memory can't pile up; a guess
