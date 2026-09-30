"""The worker processes every piece of PDF work runs in, never the event loop.

pebble rather than the stdlib pool: it kills a hung worker, where
concurrent.futures can only stop waiting for one. A hostile PDF can hang MuPDF,
eat memory or crash it. A hung worker is killed at its timeout, memory past
the cap fails in the worker, not the server, and a crash takes only its
worker; each comes back as the Problem that says which.

pebble gives up on the whole pool when a worker dies between tasks (the kernel
killed it, say), so the pool is replaced when that happens.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import sys
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from types import ModuleType
from typing import cast

from fastapi import Request
from pebble import ProcessExpired, ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import TooSlow
from squidpdf.core import Damaged, Problem, TooHeavy, result_of

__all__ = [
    "Pool",
    "current",
]

# pebble's failures, and the Problem each one means. In order: the first that matches wins.
_FAILURES: list[tuple[type[Exception], Callable[[Exception], Problem]]] = [
    # Out of time, waiting or working: slow, not necessarily broken.
    (TimeoutError, lambda _failure: TooSlow()),
    (MemoryError, lambda _failure: TooHeavy()),  # past the memory ceiling
    (ProcessExpired, lambda _failure: Damaged()),  # the worker died: MuPDF crashed on the file
]
_FAILED = tuple(raised for raised, _make in _FAILURES)  # the types alone, for `except`


class Pool:
    """Workers for PDF work. Tasks take file paths: an open document doesn't pickle."""

    def __init__(self) -> None:
        """Set the pool up; pebble starts the workers on the first task."""
        self._pool = process_pool()
        # One replacement at a time: two tasks that find the pool broken build one new pool.
        self._replacing = asyncio.Lock()

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, killed after `timeout` seconds.

        If the request goes away first, the task is cancelled and pebble stops
        the worker running it. The PDF library's own failures come back as the
        Problems they mean (`core.result_of`), and pebble's by `_FAILURES`.
        """
        pool = await self._running()
        future = pool.submit(partial(result_of, task), timeout)
        try:
            return await asyncio.wrap_future(future)
        except _FAILED as failure:  # pebble's, listed in _FAILURES
            raise problem_of(failure) from failure

    async def ready(self) -> bool:
        """Whether workers can take a task, replacing them first if the pool broke."""
        try:
            return (await self._running()).active  # starts a new pool's workers
        except BrokenProcessPool:  # raised by pebble when it can't start a worker process
            return False

    async def _running(self) -> ProcessPool:
        """The pool, or a new one if a worker died between tasks and broke it."""
        async with self._replacing:
            if not self._pool.active:  # pebble stops taking tasks for good after that
                broken, self._pool = self._pool, process_pool()
                broken.stop()
                await asyncio.to_thread(broken.join)  # waits for pebble's own threads
            return self._pool

    def close(self) -> None:
        """Stop the workers, dropping queued tasks: nobody is waiting for them now."""
        self._pool.stop()
        self._pool.join()


def process_pool() -> ProcessPool:
    """pebble's pool of worker processes, not started yet."""
    return ProcessPool(
        max_workers=constants.WORKERS,
        max_tasks=constants.TASKS_PER_WORKER,
        initializer=limit_memory,
        # Spawn: a fork of a threaded server can inherit a held lock and hang.
        # The cast because pebble types `context` as a module; it takes any.
        context=cast(ModuleType, multiprocessing.get_context("spawn")),
    )


def problem_of(failure: Exception) -> Problem:
    """What one of pebble's failures means: the first row of `_FAILURES` it matches."""
    return next(make(failure) for raised, make in _FAILURES if isinstance(failure, raised))


def current(request: Request) -> Pool:
    """The app's pool, started with it. A route's dependency."""
    return request.app.state.pool


def limit_memory() -> None:
    """Cap a worker's memory, so a hostile PDF runs out in its worker, not the server.

    Linux only. macOS doesn't enforce RLIMIT_AS, so development runs uncapped.
    """
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
