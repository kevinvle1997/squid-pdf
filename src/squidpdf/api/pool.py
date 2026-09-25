"""The worker processes every piece of PDF work runs in, never the event loop.

pebble rather than the stdlib pool: it kills a hung worker, where
concurrent.futures can only stop waiting for one. A hostile PDF can hang MuPDF
or eat memory; either way its worker dies and the request says damaged.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import sys
from collections.abc import Callable
from types import ModuleType
from typing import cast

from fastapi import Request
from pebble import ProcessExpired, ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import ApiError, Problem


class Pool:
    """Workers for PDF work. Tasks take file paths: an open document doesn't pickle."""

    def __init__(self) -> None:
        """Set the pool up; pebble starts the workers on the first task."""
        self._pool = ProcessPool(
            max_tasks=constants.TASKS_PER_WORKER,
            initializer=_limit_memory,
            # Spawn, not fork: forking a server that already runs threads can
            # copy a held lock into the worker, which then hangs on it.
            # pebble annotates `context` as the module but takes any context.
            context=cast(ModuleType, multiprocessing.get_context("spawn")),
        )

    async def run[**P, T](
        self, timeout: float, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """`fn(*args, **kwargs)` in a worker, killed after `timeout` seconds.

        If the request goes away first, the task is cancelled and pebble stops
        the worker running it.
        """
        future = self._pool.submit(fn, timeout, *args, **kwargs)
        try:
            return await asyncio.wrap_future(future)
        # pebble raises these for a task out of time, or a worker that died;
        # the task itself raises MemoryError at the ceiling.
        except (TimeoutError, ProcessExpired, MemoryError) as exc:
            raise ApiError(Problem.DAMAGED) from exc

    def close(self) -> None:
        """Stop the workers, dropping queued tasks: nobody is waiting for them now."""
        self._pool.stop()
        self._pool.join()


def current(request: Request) -> Pool:
    """The app's pool, started with it. A route's dependency."""
    return request.app.state.pool


def _limit_memory() -> None:
    """Cap a worker's memory, so a hostile PDF kills its worker, not the server.

    Linux only. macOS doesn't enforce RLIMIT_AS, so development runs uncapped.
    """
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
