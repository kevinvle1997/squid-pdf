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
from pebble import ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import TooSlow
from squidpdf.core import Problem, result_of

__all__ = [
    "WorkerPool",
    "current",
]


class WorkerPool:
    """Workers for PDF work. Tasks take file paths: an open document doesn't pickle."""

    def __init__(self) -> None:
        """Set the pool up; pebble starts the workers on the first task."""
        self._pool = process_pool()
        # One replacement at a time: two tasks that find the pool broken build one new pool.
        self._replacing = asyncio.Lock()
        # Tasks wait for a worker here, not in pebble's queue: leaving from here costs nothing.
        self._free = asyncio.Semaphore(constants.WORKERS)
        # Tasks at work, held: asyncio keeps only a weak reference to one nobody awaits.
        self._jobs: set[asyncio.Task[object]] = set()

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, given `timeout` seconds from this call.

        Time spent waiting for a free worker counts; a new worker's start doesn't,
        since that's the server's time, not the task's. If the caller goes away
        first, a task still waiting is dropped, and a long one (`STOP_WHEN_LEFT_S`)
        is stopped. A short one finishes: stopping it would kill its worker, and
        the next task would wait for a new one. The PDF library's own failures
        come back as the Problems they mean (`core.result_of`), and pebble's by
        `constants.WORKER_FAILURES`.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                await self._free.acquire()
        except TimeoutError as waited:  # no worker came free in time
            raise TooSlow() from waited
        time_left = deadline - loop.time()
        if time_left <= 0:  # free just as time ran out; pebble reads 0 as no timeout at all
            self._free.release()
            raise TooSlow()
        # Created at once, and the worker given back when it's done, however it ends.
        job = asyncio.create_task(self._in_worker(time_left, task))
        self._jobs.add(job)
        job.add_done_callback(self._given_back)
        try:
            await asyncio.wait([job])  # unlike awaiting the job, leaving here doesn't cancel it
        except asyncio.CancelledError:  # raised when the caller leaves before the answer
            # A long task stops; pebble holds a short one to its time.
            if timeout >= constants.STOP_WHEN_LEFT_S:
                job.cancel()  # does nothing to a task that has finished
            job.add_done_callback(log_unexpected)
            raise
        try:
            return job.result()
        except constants.WORKER_FAILURE_TYPES as failure:  # raised by pebble, one per row
            raise problem_of(failure) from failure

    def _given_back[T](self, job: asyncio.Task[T]) -> None:
        """A task is done, however it ended: its worker is free for the next."""
        self._free.release()
        self._jobs.discard(job)

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

    async def _in_worker[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` on a worker set aside for it, killed by pebble `timeout` seconds in."""
        pool = await self._running()
        return await asyncio.wrap_future(pool.submit(partial(result_of, task), timeout))

    def close(self) -> None:
        """Stop the workers, dropping queued tasks: nobody is waiting for them now."""
        for job in self._jobs:  # a task whose caller left: it ends as cancelled, not as failed
            job.cancel()
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


def log_unexpected[T](job: asyncio.Task[T]) -> None:
    """Log how a task nobody waits for now failed, unless pebble's usual failures say it."""
    if job.cancelled():
        return
    failure = job.exception()  # read here, so asyncio doesn't log it as never read
    expected = failure is None or isinstance(failure, constants.WORKER_FAILURE_TYPES)
    if expected:
        return
    message = "a task whose caller left failed"
    job.get_loop().call_exception_handler(
        {"message": message, "exception": failure, "task": job}
    )


def problem_of(failure: Exception) -> Problem:
    """What one of pebble's failures means: the first row of `WORKER_FAILURES` it matches."""
    return next(
        worker_failure.problem()
        for worker_failure in constants.WORKER_FAILURES
        if isinstance(failure, worker_failure.raised)
    )


def current(request: Request) -> WorkerPool:
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
