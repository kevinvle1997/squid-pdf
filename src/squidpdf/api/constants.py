"""What the server will accept, how hard it works, and what a worker's failure means.

The numbers are starting values, not findings. They protect the server, not the
business, so no plan or account lifts them.
"""

from __future__ import annotations

import os
from concurrent.futures.process import BrokenProcessPool

from pebble import ProcessExpired

from squidpdf.api.errors import NoWorkers, TooSlow
from squidpdf.core import Damaged, Failure, TooHeavy

_MB = 1024 * 1024

# Page images: the scales a route accepts. The pixel limit is documents' own.
PAGE_SCALES = (1, 2, 3, 4)

# Uploads. Their size and page limits are documents' own.
UPLOADS_PER_MINUTE = 20  # per IP

# Every request body but an upload, which checks its own size.
MAX_BODY_BYTES = 5 * _MB

# Attached fonts: read by font attach (#52) when it's built.
MAX_FONT_BYTES = 25 * _MB
MAX_FONTS = 20  # per document

# Workers. Each feature's timeouts are in its own constants.py.
WORKERS = os.process_cpu_count() or 1  # PDF work keeps a core busy: one each
WORKER_MEMORY_BYTES = 1024 * _MB
TASKS_PER_WORKER = 100  # then replaced, so leaked memory can't pile up; a guess
# A task allowed this long (an export, an analysis) is stopped when its caller leaves.
# A shorter one (a render, a page image) finishes: stopping it kills its worker.
STOP_WHEN_LEFT_S = 30


# Worker failures, matched in order with `isinstance`, and the first match wins.
# `isinstance`, not a dict keyed by type: a subclass of a row's type must find it.
# The order, because a new row could overlap one above it: the narrower goes first.
WORKER_FAILURES = (
    # Out of time, waiting or working: slow, not necessarily broken.
    Failure(raised=TimeoutError, problem=TooSlow),
    Failure(raised=MemoryError, problem=TooHeavy),  # past the memory ceiling
    # The worker died: MuPDF crashed on the file.
    Failure(raised=ProcessExpired, problem=Damaged),
    # pebble gave up on the pool under the task, or no worker could start or stay up.
    Failure(raised=BrokenProcessPool, problem=NoWorkers),
)
# The types alone, for an `except` or an `isinstance`.
WORKER_FAILURE_TYPES = tuple(failure.raised for failure in WORKER_FAILURES)
