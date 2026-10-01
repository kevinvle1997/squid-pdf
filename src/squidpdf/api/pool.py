"""The worker processes every piece of PDF work runs in, never the event loop.

pebble rather than the stdlib pool: it kills a hung worker, where
concurrent.futures can only stop waiting for one. A hostile PDF can hang MuPDF,
eat memory or crash it. A hung worker is killed at its timeout, memory past
the cap fails in the worker, not the server, and a crash takes only its
worker; each comes back as the Problem that says which.

pebble gives up on the whole pool when a worker dies between tasks (the kernel
killed it, say), so the pool is replaced when that happens. A task it gave up
on goes again, once, on the new pool, but only if no worker had started it:
each try notes its start in a file of its own, read once the old workers are gone.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import cast

from fastapi import Request
from pebble import ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import TooSlow
from squidpdf.api.errors.http import API_ERRORS
from squidpdf.core import result_of

__all__ = [
    "WorkerPool",
    "current",
]


class NeverStarted(Exception):
    """The pool broke before any worker started the task, so the task did no work."""


class WorkerPool:
    """Workers for PDF work. Tasks take file paths: an open document doesn't pickle.

    Made in a running event loop, and used only in that one: the app's lifespan
    makes it, in the server's one loop.
    """

    def __init__(self) -> None:
        """Set the pool up in the running loop; pebble starts the workers on the first task.

        Raises RuntimeError when no loop is running.
        """
        # The loop its lock and semaphore work in. asyncio ties each to the first loop
        # that waits on it, so a pool two loops share would fail only once it's busy;
        # tied to the loop it's made in, another loop is refused on its first task.
        self._loop = asyncio.get_running_loop()
        self._pool = process_pool()
        # Where each try of a task notes that a worker started it; removed with the pool.
        self._starts = Path(tempfile.mkdtemp(prefix="squidpdf-starts-"))
        # One replacement at a time: two tasks that find the pool broken build one new pool.
        self._replacing = asyncio.Lock()
        # Tasks wait for a worker here, not in pebble's queue: leaving from here costs nothing.
        self._free = asyncio.Semaphore(constants.WORKERS)
        # Tasks at work, held: asyncio keeps only a weak reference to one nobody awaits.
        self._jobs: set[asyncio.Task[object]] = set()

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, given `timeout` seconds from this call.

        Time spent waiting for a free worker counts; a new worker's start doesn't,
        since that's the server's time, not the task's. A task the pool broke under
        before any worker started it goes again, once, on a new pool, to the same
        deadline; one a worker started never does. If the caller goes away
        first, a task still waiting is dropped, and a long one (`STOP_WHEN_LEFT_S`)
        is stopped. A short one finishes: stopping it would kill its worker, and
        the next task would wait for a new one. The PDF library's own failures
        come back as the Problems they mean (`core.result_of`), and pebble's by
        `API_ERRORS`, which holds `constants.WORKER_FAILURES`.
        """
        self._require_own_loop()
        deadline = self._loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                await self._free.acquire()
        except TimeoutError as waited:  # no worker came free in time
            raise TooSlow() from waited
        # Created at once, and the worker given back when it's done, however it ends.
        job = asyncio.create_task(self._in_worker(deadline, task))
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
        # pebble's failures as the Problems they mean; a bug goes up as it is, to be logged.
        return API_ERRORS.result_of(job.result)

    def _require_own_loop(self) -> None:
        """Raise RuntimeError unless called in the loop the pool was made in."""
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("a WorkerPool works only in the event loop it was made in")

    def _given_back[T](self, job: asyncio.Task[T]) -> None:
        """A task is done, however it ended: its worker is free for the next."""
        self._free.release()
        self._jobs.discard(job)

    async def ready(self) -> bool:
        """Whether workers can take a task, replacing them first if the pool broke."""
        self._require_own_loop()
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

    async def _in_worker[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` on a worker set aside for it, killed by pebble at `deadline`.

        Sent a second time, on a new pool, if the pool broke before any worker
        started it: it did no work, so nothing runs twice. Never a third.
        """
        try:
            return await self._sent(deadline, task)
        except NeverStarted:  # raised when the pool broke before a worker started the task
            return await self._sent(deadline, task)

    async def _sent[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` sent to the pool, a new one if it broke, killed by pebble at `deadline`.

        Raises NeverStarted when the pool breaks before any worker starts it.
        """
        # Worked out before a broken pool is replaced: that's the server's time, not the task's.
        time_left = deadline - self._loop.time()
        if time_left <= 0:  # out of time already; pebble reads 0 as no timeout at all
            raise TooSlow()
        pool = await self._running()
        start = self._starts / uuid.uuid4().hex  # this try's own: the worker makes it
        try:
            future = pool.submit(partial(noted_start, str(start), task), time_left)
        except RuntimeError as refused:  # raised by pebble when the pool broke since `_running`
            raise NeverStarted() from refused
        try:
            return await asyncio.wrap_future(future)
        except BrokenProcessPool as broke:  # raised by pebble when it gives up on the pool
            # Its workers stopped first, so none can still start the task once it's looked at.
            await self._running()
            if not has_started(start):
                raise NeverStarted() from broke
            raise
        finally:
            forget_start(start)

    def close(self) -> None:
        """Stop the workers, dropping queued tasks: nobody is waiting for them now."""
        for job in self._jobs:  # a task whose caller left: it ends as cancelled, not as failed
            job.cancel()
        self._pool.stop()
        self._pool.join()
        shutil.rmtree(self._starts, ignore_errors=True)


def noted_start[T](start: str, task: Callable[[], T]) -> T:
    """In a worker: note that it started `task` by making the file `start`, then run it.

    The library's failures come back as the Problems they mean (`core.result_of`).
    """
    Path(start).touch()
    return result_of(task)


def has_started(start: Path) -> bool:
    """Whether a worker made the file a try's `noted_start` was given."""
    return start.exists()


def forget_start(start: Path) -> None:
    """Remove a try's start file, made or not: the try is over."""
    start.unlink(missing_ok=True)


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
