"""The worker processes all PDF work runs in, never the event loop.

pebble, not the stdlib pool, since it kills a hung worker: a hostile PDF fails in its worker.
A change that breaks a guarantee below changes this list in the same diff.

- Waiting for a worker counts toward the timeout; its start doesn't, up to `WORKER_START_S`.
- A caller who leaves drops a waiting task, and stops one timed at `STOP_WHEN_LEFT_S` or more.
- A caller who leaves is let go once its worker is, so what counts its jobs counts it till then.
- A broken pool is replaced before the next task, by just one new pool; `ready` does it too.
- A task the pool broke under or lost before it started goes again, once; none runs twice.
- A task it can't run, or broke under once started, is `no_workers`; the cause is logged.
- An answer that can't make the trip back is a server error, and the pool works on.
- A pool works only in the event loop it was made in: its lock and semaphore bind to it.
- pebble's failures become Problems through `constants.WORKER_FAILURES`.
- A request's line tallies its wait for a worker, and its task's tallies, answered or failed.
- A task killed at its timeout sends no tallies back.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import io
import multiprocessing
import pickle
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import cast

from fastapi import Request, status
from pebble import ProcessFuture, ProcessPool

from squidpdf.api import constants
from squidpdf.api.errors import NoWorkers, TooSlow
from squidpdf.api.errors.http import API_ERRORS
from squidpdf.core import (
    WARN,
    LogController,
    LogEvent,
    Problem,
    Tallies,
    Tally,
    WorkerAnswer,
    add_from_worker,
    carried,
    current_request_id,
    in_request,
    ms_since,
    result_of,
    tallies_carried_by,
    tally,
)

_log = LogController.for_module(__name__)

_EXPECTED_WHEN_LEFT: tuple[type[Exception], ...] = (Problem, *constants.WORKER_FAILURE_TYPES)


@dataclass(slots=True)
class _WorkerWarmth:
    warm: bool = False

    def warm_up(self) -> bool:
        was_cold, self.warm = not self.warm, True
        return was_cold


# Never reset: a new worker is a new process.
_THIS_WORKER = _WorkerWarmth()


class _NeverStarted(Exception):
    """The pool broke before any worker started the task, so the task did no work."""


class _CantMakeTheTrip(Exception):
    """Made with one message, so it makes the trip itself."""


@dataclass(slots=True)
class _ProcessSlot:
    pool: ProcessPool

    def replace(self) -> ProcessPool:
        broken, self.pool = self.pool, _process_pool()
        return broken


@dataclass(frozen=True, slots=True, eq=False)
class WorkerPool:
    """Tasks take file paths: an open document doesn't pickle."""

    # The loop its lock and semaphore work in: asyncio ties each to the first loop that waits
    # on it, failing only once the pool is busy; checked here, another is refused at once.
    loop: asyncio.AbstractEventLoop
    slot: _ProcessSlot
    starts: Path
    replacing: asyncio.Lock
    # Tasks wait for a worker here, not in pebble's queue: leaving from here costs nothing.
    free: asyncio.Semaphore
    # Tasks at work, held: asyncio keeps only a weak reference to one nobody awaits.
    jobs: set[asyncio.Task[object]] = field(default_factory=set, repr=False)

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        self._require_own_loop()
        deadline = self.loop.time() + timeout
        waited_from = time.monotonic()
        try:
            async with asyncio.timeout_at(deadline):
                await self.free.acquire()
        except TimeoutError as waited:
            raise TooSlow() from waited
        finally:
            tally(Tally.QUEUED_MS, ms_since(waited_from))
        job = asyncio.create_task(self._in_worker(deadline, task))
        self.jobs.add(job)
        job.add_done_callback(self._given_back)
        try:
            await asyncio.wait([job])  # unlike awaiting the job, leaving here doesn't cancel it
        except asyncio.CancelledError:
            if timeout >= constants.STOP_WHEN_LEFT_S:
                job.cancel()
            job.add_done_callback(_log_unexpected)
            # Its worker is busy till then: a turn held by the caller lasts as long.
            await asyncio.wait([job])
            raise
        return API_ERRORS.result_of(job.result)

    def _require_own_loop(self) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("a WorkerPool works only in the event loop it was made in")

    def _given_back[T](self, job: asyncio.Task[T]) -> None:
        self.free.release()
        self.jobs.discard(job)

    async def ready(self) -> bool:
        self._require_own_loop()
        try:
            return _is_active(await self._running())
        except BrokenProcessPool:
            return False

    async def _running(self) -> ProcessPool:
        async with self.replacing:
            if not _is_active(self.slot.pool):
                broken = self.slot.replace()
                broken.stop()
                await asyncio.to_thread(broken.join)
            return self.slot.pool

    async def _in_worker[T](self, deadline: float, task: Callable[[], T]) -> T:
        with contextlib.suppress(_NeverStarted):
            return await self._sent(deadline, task)
        try:
            return await self._sent(deadline, task)
        except _NeverStarted as again:
            raise NoWorkers() from again

    async def _sent[T](self, deadline: float, task: Callable[[], T]) -> T:
        # Worked out before a broken pool is replaced: that's the server's time, not the task's.
        time_left = deadline - self.loop.time()
        if time_left <= 0:  # out of time already; pebble reads 0 as no timeout at all
            raise TooSlow()
        try:
            pool = await self._running()
        except BrokenProcessPool as broke:
            _log.write(LogEvent.NO_WORKER_STARTED, broke)
            raise
        start = self.starts / uuid.uuid4().hex
        try:
            job = partial(_noted_start, start, request_id=current_request_id(), task=task)
            future = pool.submit(job, time_left)
        except (RuntimeError, OSError) as refused:
            _log.write(LogEvent.POOL_REFUSED_TASK, refused)
            pool.stop()
            raise _NeverStarted() from refused
        # Only for a wait pebble never answers on an unbroken pool: its timeout comes first.
        backstop = asyncio.timeout(time_left + constants.WORKER_START_S)
        try:
            async with backstop:
                return await self._tallied_answer(pool, future)
        except TimeoutError:
            if backstop.expired():
                _log.write(LogEvent.POOL_NEVER_ANSWERED)
            raise
        except BrokenProcessPool as broke:
            _log.write(LogEvent.POOL_BROKE, broke)
            # Its workers stopped first, so none can still start the task once it's looked at.
            await self._running()
            if not _has_started(start):
                raise _NeverStarted() from broke
            raise
        finally:
            _forget_start(start)

    async def _tallied_answer[T](
        self, pool: ProcessPool, future: ProcessFuture[WorkerAnswer[bytes]]
    ) -> T:
        try:
            answer = await self._answer_from(pool, future)
        except Exception as failure:
            add_from_worker(tallies_carried_by(failure))
            raise
        add_from_worker(answer.tallies)
        result = await asyncio.to_thread(_read_back, answer.result)
        return cast(T, result)

    async def _answer_from(
        self, pool: ProcessPool, future: ProcessFuture[WorkerAnswer[bytes]]
    ) -> WorkerAnswer[bytes]:
        """Raises BrokenProcessPool if `pool` loses it: pebble never says so."""
        answer = asyncio.wrap_future(future)
        try:
            while pool.active:
                await asyncio.wait([answer], timeout=constants.BROKEN_POOL_CHECK_S)
                if answer.done():
                    return answer.result()
            # Its threads joined first, so nothing answers the task once it's looked at.
            await self._running()
            if not future.done():
                raise BrokenProcessPool("The worker pool broke and lost a task")
            return await answer
        finally:
            answer.cancel()

    async def close(self) -> None:
        self._require_own_loop()
        for job in self.jobs:
            job.cancel()
        self.slot.pool.stop()
        await asyncio.to_thread(self.slot.pool.join)
        shutil.rmtree(self.starts, ignore_errors=True)


def start_pool() -> WorkerPool:
    loop = asyncio.get_running_loop()
    return WorkerPool(
        loop,
        _ProcessSlot(_process_pool()),
        starts=Path(tempfile.mkdtemp(prefix="squidpdf-starts-")),
        replacing=asyncio.Lock(),
        free=asyncio.Semaphore(constants.WORKERS),
    )


def _noted_start[T](
    start: Path, *, request_id: str | None, task: Callable[[], T]
) -> WorkerAnswer[bytes]:
    """In a worker; pickles the result here, so pebble's threads never read one of ours."""
    start.touch()
    try:
        answer = in_request(request_id, partial(_run_tallied, task))
    except Exception as failure:
        _require_trip(failure)
        raise
    return WorkerAnswer(_pickled(answer.result, tallies=answer.tallies), answer.tallies)


def _pickled(result: object, *, tallies: Tallies) -> bytes:
    # Into a stream, not `pickle.dumps`, whose buffer grows past the result's size.
    sent_back = io.BytesIO()
    try:
        pickle.Pickler(sent_back, protocol=pickle.HIGHEST_PROTOCOL).dump(result)
    except MemoryError:
        raise
    except Exception as problem:
        unsent = _cant_make_the_trip(type(result).__name__, problem)
        raise carried(unsent, tallies) from problem
    return sent_back.getvalue()


def _require_trip(failure: Exception) -> None:
    try:
        pickle.loads(pickle.dumps(failure))  # our own bytes, made just now
    except MemoryError:
        raise
    except Exception as problem:
        unsent = _cant_make_the_trip(type(failure).__name__, problem)
        raise carried(unsent, tallies_carried_by(failure)) from problem


def _cant_make_the_trip(kind: str, problem: Exception) -> _CantMakeTheTrip:
    return _CantMakeTheTrip(f"{kind} can't make the trip back: {type(problem).__name__}")


def _read_back(sent_back: bytes) -> object:
    try:
        return pickle.loads(sent_back)  # our own worker's, made by `_pickled`
    except Exception as problem:
        raise _cant_make_the_trip("a task's result", problem) from problem


def _run_tallied[T](task: Callable[[], T]) -> T:
    if _THIS_WORKER.warm_up():
        tally(Tally.COLD)
    began = time.monotonic()
    try:
        return result_of(task)
    finally:
        tally(Tally.WORKER_MS, ms_since(began))


def _is_active(pool: ProcessPool) -> bool:
    try:
        return pool.active
    except (OSError, RuntimeError) as cant_start:
        # Started again, it would wire its workers to the pipes of the start that failed.
        pool.stop()
        raise BrokenProcessPool("The worker pool couldn't start") from cant_start


def _has_started(start: Path) -> bool:
    return start.exists()


def _forget_start(start: Path) -> None:
    start.unlink(missing_ok=True)


def _process_pool() -> ProcessPool:
    return ProcessPool(
        max_workers=constants.WORKERS,
        max_tasks=constants.TASKS_PER_WORKER,
        initializer=_start_worker,
        # Spawn: a fork of a threaded server can inherit a held lock and hang.
        # The cast because pebble types `context` as a module; it takes any.
        context=cast(ModuleType, multiprocessing.get_context("spawn")),
    )


def _log_unexpected[T](job: asyncio.Task[T]) -> None:
    if job.cancelled():
        return
    failure = job.exception()  # read here, so asyncio doesn't log it as never read
    if failure is None:
        return
    if _has_server_debug(failure):
        _log.write(LogEvent.TASK_FAILED_CALLER_LEFT, failure, level=WARN)
        return
    if isinstance(failure, _EXPECTED_WHEN_LEFT):
        return
    _log.write(LogEvent.TASK_FAILED_CALLER_LEFT, failure)


def _has_server_debug(failure: BaseException) -> bool:
    if not isinstance(failure, Problem):
        return False
    on_our_side = failure.status >= status.HTTP_500_INTERNAL_SERVER_ERROR
    return on_our_side and failure.debug is not None


def current(request: Request) -> WorkerPool:
    return request.app.state.pool


def _start_worker() -> None:
    LogController.configure()
    _limit_memory()
    gc.set_threshold(constants.WORKER_GC_THRESHOLD)


def _limit_memory() -> None:
    """macOS doesn't enforce RLIMIT_AS, so development runs uncapped."""
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
