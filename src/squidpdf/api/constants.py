"""They protect the server, not the business, so no plan or account lifts them."""

from __future__ import annotations

import os
from concurrent.futures.process import BrokenProcessPool

from pebble import ProcessExpired

from squidpdf.api.errors import NoWorkers, TooSlow
from squidpdf.core import Damaged, Failure, TooHeavy

_MB = 1024 * 1024

PAGE_SCALES = (1, 2, 3, 4)

UPLOADS_PER_MINUTE = 20  # per IP
# Per IP, PDFs and fonts together: each holds disk against the floor until it's in.
UPLOADS_UNDER_WAY = 2

# Per IP: a mistyped address asks twice; a page asking for every address is stopped.
LINKS_PER_MINUTE = 3
# Per inbox while a link works (LINK_VALID_S): a first and a resend.
LINKS_PER_INBOX = 2

# Per IP, checkout and portal together: each holds one of billing's few threads.
PAYMENT_PAGES_PER_MINUTE = 5

MAX_BODY_BYTES = 5 * _MB

# Past those at work, the rest wait holding no worker: page images near the view ask at once.
JOBS_AT_WORK = 3  # per IP
JOBS_WAITING = 30  # per IP, beyond those at work; past both, refused

WORKERS = os.process_cpu_count() or 1  # PDF work keeps a core busy: one each
WORKER_MEMORY_BYTES = 1024 * _MB
# 25 times Python's default: a page's letters are many small, short-lived objects.
WORKER_GC_THRESHOLD = 50_000
# Then replaced, before creeping memory nears the cap; rarely, as a new one refills caches.
TASKS_PER_WORKER = 500
# A task this long stops when its caller leaves; a shorter one finishes: stopping kills it.
STOP_WHEN_LEFT_S = 30
WORKER_START_S = 30
# As often as pebble checks: a pool that breaks as a task is sent can lose it and say nothing.
BROKEN_POOL_CHECK_S = 0.1

# Matched in order with `isinstance`: a narrower row goes above one it overlaps.
WORKER_FAILURES = (
    Failure(raised=TimeoutError, problem=TooSlow),
    Failure(raised=MemoryError, problem=TooHeavy),
    Failure(raised=ProcessExpired, problem=Damaged),
    Failure(raised=BrokenProcessPool, problem=NoWorkers),
)
WORKER_FAILURE_TYPES = tuple(failure.raised for failure in WORKER_FAILURES)
