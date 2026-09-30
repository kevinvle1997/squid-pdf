"""What the server will accept and how hard it works. Starting values, not findings.

These protect the server, not the business, so no plan or account lifts them.
"""

from __future__ import annotations

import os

_MB = 1024 * 1024

# Page images: the scales a route accepts. The pixel limit is documents' own.
PAGE_SCALES = (1, 2, 3, 4)

# Uploads. Their size and page limits are documents' own.
UPLOADS_PER_MINUTE = 20  # per IP

# Every request body but an upload, which checks its own size.
MAX_BODY_BYTES = 5 * _MB

# Attached fonts.
MAX_FONT_BYTES = 25 * _MB
MAX_FONTS = 20  # per document

# Workers. Each feature's timeouts are in its own constants.py.
WORKERS = os.process_cpu_count() or 1  # PDF work keeps a core busy: one each
WORKER_MEMORY_BYTES = 1024 * _MB
TASKS_PER_WORKER = 100  # then replaced, so leaked memory can't pile up; a guess
# A task allowed this long (an export, an analysis) is stopped when its caller leaves.
# A shorter one (a render, a page image) finishes: stopping it kills its worker.
STOP_WHEN_LEFT_S = 30
