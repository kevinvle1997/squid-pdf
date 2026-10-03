"""The worker processes every piece of PDF work runs in, never the event loop.

pebble rather than the stdlib pool: it kills a hung worker, where
concurrent.futures can only stop waiting for one. A hostile PDF can hang MuPDF,
eat memory or crash it. A hung worker is killed at its timeout, memory past
the cap fails in the worker, not the server, and a crash takes only its
worker; each comes back as the Problem that says which.

What it guarantees. A change that breaks one changes this list in the same diff.

- Time a task spends waiting for a worker counts toward its timeout, since the
  wait is the caller's. A new worker's start doesn't: that's the server's.
- When the caller leaves, a task still waiting is dropped, and a long one
  (timeout at or past `STOP_WHEN_LEFT_S`) is stopped. A short one finishes in
  its worker: stopping it would kill the worker, and the next task would wait
  for a new one.
- pebble gives up on the whole pool when a worker dies between tasks (the
  kernel killed it, say). A broken pool is replaced before the next task, under
  a lock, so two tasks that find it broken build one new pool. `/api/health`
  goes through the same check (`WorkerPool.ready`).
- A task the pool broke under before any worker started it goes again, once,
  on the new pool and to the same deadline. The worker makes the try's start
  file before running the task, read once the old workers are gone, and only a
  try without one is retried, so a started task never runs twice.
- A task the pool can't run, because the new pool broke too, no worker can
  start or stay up, or the pool broke under it once started, is `no_workers`:
  the server's failure, not a bug and not the file's. The break, or the
  failed start, is logged with its cause, which can be a bug of ours (a result
  that can't be sent back).
- A pool works only in the event loop it was made in (the lifespan's), and
  refuses any other: its lock and semaphore bind to that loop.
- pebble's failures become Problems through `constants.WORKER_FAILURES`, asked
  via `API_ERRORS`, the API's one ErrorController. The PDF library's own come
  back as the Problems they mean (`core.result_of`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import cast

from fastapi import Request
from pebble import ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import NoWorkers, TooSlow
from squidpdf.api.errors.http import API_ERRORS
from squidpdf.core import Problem, result_of

_logger = logging.getLogger(__name__)

# How a task whose caller left may fail without a bug: pebble's usual failures, and any
# Problem (too slow, a damaged file, no workers), which a caller gets as its answer, unlogged.
_EXPECTED_WHEN_LEFT: tuple[type[Exception], ...] = (Problem, *constants.WORKER_FAILURE_TYPES)


class _NeverStarted(Exception):
    """The pool broke before any worker started the task, so the task did no work."""


@dataclass(slots=True)
class _ProcessSlot:
    """pebble's worker processes: the part of a WorkerPool that's replaced when it breaks."""

    pool: ProcessPool

    def replace(self) -> ProcessPool:
        """Put a new pool in, not started yet, and hand back the broken one to stop."""
        broken, self.pool = self.pool, _process_pool()
        return broken


@dataclass(frozen=True, slots=True, eq=False)
class WorkerPool:
    """Workers for PDF work. Tasks take file paths: an open document doesn't pickle.

    Made by `start_pool` in a running event loop, and used only in that one: the
    app's lifespan makes it, in the server's one loop.
    """

    # The loop its lock and semaphore work in. asyncio ties each to the first loop
    # that waits on it, so a pool two loops share would fail only once it's busy;
    # tied to the loop it's made in, another loop is refused on its first task.
    loop: asyncio.AbstractEventLoop
    slot: _ProcessSlot  # pebble's pool, which a worker dying between tasks breaks
    # Where each try of a task notes that a worker started it; removed with the pool.
    starts: Path
    # One replacement at a time: two tasks that find the pool broken build one new pool.
    replacing: asyncio.Lock
    # Tasks wait for a worker here, not in pebble's queue: leaving from here costs nothing.
    free: asyncio.Semaphore
    # Tasks at work, held: asyncio keeps only a weak reference to one nobody awaits.
    jobs: set[asyncio.Task[object]] = field(default_factory=set, repr=False)

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, given `timeout` seconds from this call.

        What it guarantees (the wait, a caller who leaves, a broken pool, the
        retry, pebble's failures) is the module's docstring.
        """
        self._require_own_loop()
        deadline = self.loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                await self.free.acquire()
        except TimeoutError as waited:  # no worker came free in time
            raise TooSlow() from waited
        # Created at once, and the worker given back when it's done, however it ends.
        job = asyncio.create_task(self._in_worker(deadline, task))
        self.jobs.add(job)
        job.add_done_callback(self._given_back)
        try:
            await asyncio.wait([job])  # unlike awaiting the job, leaving here doesn't cancel it
        except asyncio.CancelledError:  # raised when the caller leaves before the answer
            # A long task stops; pebble holds a short one to its time.
            if timeout >= constants.STOP_WHEN_LEFT_S:
                job.cancel()  # does nothing to a task that has finished
            job.add_done_callback(_log_unexpected)
            raise
        # pebble's failures as the Problems they mean; a bug goes up as it is, to be logged.
        return API_ERRORS.result_of(job.result)

    def _require_own_loop(self) -> None:
        """Raise RuntimeError unless called in the loop the pool was made in."""
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("a WorkerPool works only in the event loop it was made in")

    def _given_back[T](self, job: asyncio.Task[T]) -> None:
        """A task is done, however it ended: its worker is free for the next."""
        self.free.release()
        self.jobs.discard(job)

    async def ready(self) -> bool:
        """Whether the pool takes tasks, once replaced if it broke and its workers started.

        Busy workers still count: a task sent now waits for one to come free.
        """
        self._require_own_loop()
        try:
            return (await self._running()).active  # starts a new pool's workers
        except BrokenProcessPool:  # raised by pebble when it can't start a worker process
            return False

    async def _running(self) -> ProcessPool:
        """The pool, or a new one if a worker died between tasks and broke it."""
        async with self.replacing:
            if not self.slot.pool.active:  # pebble stops taking tasks for good after that
                broken = self.slot.replace()
                broken.stop()
                await asyncio.to_thread(broken.join)  # waits for pebble's own threads
            return self.slot.pool

    async def _in_worker[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` on a worker set aside for it, killed by pebble at `deadline`.

        Sent a second time, on a new pool, if the pool broke before any worker
        started it: it did no work, so nothing runs twice. Never a third: if the
        new pool breaks the same way, no worker can take it.
        """
        with contextlib.suppress(_NeverStarted):  # the pool broke before a worker started it
            return await self._sent(deadline, task)
        try:
            return await self._sent(deadline, task)
        except _NeverStarted as again:  # raised when the new pool broke the same way
            raise NoWorkers() from again

    async def _sent[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` sent to the pool, a new one if it broke, killed by pebble at `deadline`.

        Raises _NeverStarted when the pool breaks before any worker starts it, and
        pebble's BrokenProcessPool when no worker can start, or the pool broke under
        the task once started: `WORKER_FAILURES` calls that `no_workers`.
        """
        # Worked out before a broken pool is replaced: that's the server's time, not the task's.
        time_left = deadline - self.loop.time()
        if time_left <= 0:  # out of time already; pebble reads 0 as no timeout at all
            raise TooSlow()
        try:
            pool = await self._running()
        except BrokenProcessPool as broke:  # raised by pebble when no worker process can start
            _logger.warning("No worker could start", exc_info=broke)
            raise
        start = self.starts / uuid.uuid4().hex  # this try's own: the worker makes it
        try:
            future = pool.submit(partial(_noted_start, str(start), task), time_left)
        except RuntimeError as refused:  # raised by pebble: the pool broke, or no worker starts
            _logger.warning("The worker pool refused a task", exc_info=refused)
            raise _NeverStarted() from refused
        try:
            return await asyncio.wrap_future(future)
        except BrokenProcessPool as broke:  # raised by pebble when it gives up on the pool
            # Its cause can be a bug of ours, such as a result that can't be sent back.
            _logger.warning("The worker pool broke under a task", exc_info=broke)
            # Its workers stopped first, so none can still start the task once it's looked at.
            await self._running()
            if not _has_started(start):
                raise _NeverStarted() from broke
            raise
        finally:
            _forget_start(start)

    async def close(self) -> None:
        """Stop the workers, dropping queued tasks: nobody is waiting for them now."""
        self._require_own_loop()
        for job in self.jobs:  # a task whose caller left: it ends as cancelled, not as failed
            job.cancel()
        self.slot.pool.stop()
        # Off the loop: pebble stops each worker in turn, waiting up to seconds for each.
        await asyncio.to_thread(self.slot.pool.join)
        shutil.rmtree(self.starts, ignore_errors=True)


def start_pool() -> WorkerPool:
    """A pool for the running loop; pebble starts the workers on the first task.

    Raises RuntimeError when no loop is running.
    """
    loop = asyncio.get_running_loop()
    return WorkerPool(
        loop,
        _ProcessSlot(_process_pool()),
        starts=Path(tempfile.mkdtemp(prefix="squidpdf-starts-")),
        replacing=asyncio.Lock(),
        free=asyncio.Semaphore(constants.WORKERS),
    )


def _noted_start[T](start: str, task: Callable[[], T]) -> T:
    """In a worker: note that it started `task` by making the file `start`, then run it.

    The library's failures come back as the Problems they mean (`core.result_of`).
    """
    Path(start).touch()
    return result_of(task)


def _has_started(start: Path) -> bool:
    """Whether a worker made the file a try's `_noted_start` was given."""
    return start.exists()


def _forget_start(start: Path) -> None:
    """Remove a try's start file, made or not: the try is over."""
    start.unlink(missing_ok=True)


def _process_pool() -> ProcessPool:
    """pebble's pool of worker processes, not started yet."""
    return ProcessPool(
        max_workers=constants.WORKERS,
        max_tasks=constants.TASKS_PER_WORKER,
        initializer=_limit_memory,
        # Spawn: a fork of a threaded server can inherit a held lock and hang.
        # The cast because pebble types `context` as a module; it takes any.
        context=cast(ModuleType, multiprocessing.get_context("spawn")),
    )


def _log_unexpected[T](job: asyncio.Task[T]) -> None:
    """Log how a task nobody waits for now failed, unless the pool expects that failure."""
    if job.cancelled():
        return
    failure = job.exception()  # read here, so asyncio doesn't log it as never read
    expected = failure is None or isinstance(failure, _EXPECTED_WHEN_LEFT)
    if expected:
        return
    message = "a task whose caller left failed"
    job.get_loop().call_exception_handler(
        {"message": message, "exception": failure, "task": job}
    )


def current(request: Request) -> WorkerPool:
    """The app's pool, started with it. A route's dependency."""
    return request.app.state.pool


def _limit_memory() -> None:
    """Cap a worker's memory, so a hostile PDF runs out in its worker, not the server.

    Linux only. macOS doesn't enforce RLIMIT_AS, so development runs uncapped.
    """
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
