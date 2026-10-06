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
# Per IP, PDFs and copies of fonts together: each holds disk against the floor until it's in.
UPLOADS_UNDER_WAY = 2

# Every request body but an upload's or a font's, which check their own size.
MAX_BODY_BYTES = 5 * _MB

# Each address's requests to routes that run a job, so one can't hold every worker. Past
# those at work, the rest wait their turn holding none: page images near the view ask at once.
JOBS_AT_WORK = 3  # per IP
JOBS_WAITING = 30  # per IP, beyond those at work; past both, refused

# Workers. Each feature's timeouts are in its own constants.py.
WORKERS = os.process_cpu_count() or 1  # PDF work keeps a core busy: one each
WORKER_MEMORY_BYTES = 1024 * _MB
TASKS_PER_WORKER = 100  # then replaced, so leaked memory can't pile up; a guess
# A task allowed this long (an export, an analysis) is stopped when its caller leaves.
# A shorter one (a render, a page image) finishes: stopping it kills its worker.
STOP_WHEN_LEFT_S = 30
# Room for a new worker to start, beyond a task's timeout; past both, the task is too slow.
# Generous, a guess: pebble should have answered any task by then.
WORKER_START_S = 30
# As often as pebble checks: a pool that breaks as a task is sent can lose it and say nothing.
BROKEN_POOL_CHECK_S = 0.1


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
