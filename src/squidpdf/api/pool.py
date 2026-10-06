"""The worker processes every piece of PDF work runs in, never the event loop.

pebble rather than the stdlib pool: it kills a hung worker, where concurrent.futures can only
stop waiting for one. A hostile PDF that hangs MuPDF, eats memory or crashes it does so in its
worker, not the server, and comes back as the Problem that says which.

What it guarantees. A change that breaks one changes this list in the same diff.

- Waiting for a worker counts toward the timeout; its start doesn't, up to `WORKER_START_S`.
- A caller who leaves drops a waiting task, and stops one timed at `STOP_WHEN_LEFT_S` or more.
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

# Failures that are no bug once a task's caller left: a Problem is its caller's answer.
_EXPECTED_WHEN_LEFT: tuple[type[Exception], ...] = (Problem, *constants.WORKER_FAILURE_TYPES)


@dataclass(slots=True)
class _WorkerWarmth:
    """Whether a worker process has run a task yet: its first fills every cache."""

    warm: bool = False

    def warm_up(self) -> bool:
        """Mark the worker warm; whether it was cold."""
        was_cold, self.warm = not self.warm, True
        return was_cold


# This process's, read only in a worker. Never reset: a new worker is a new process.
_THIS_WORKER = _WorkerWarmth()


class _NeverStarted(Exception):
    """The pool broke before any worker started the task, so the task did no work."""


class _CantMakeTheTrip(Exception):
    """A task's result or failure that can't be pickled or read back: our bug, not pebble's.

    Made by `_cant_make_the_trip`, with one message, so it makes the trip itself.
    """


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
        waited_from = time.monotonic()
        try:
            async with asyncio.timeout_at(deadline):
                await self.free.acquire()
        except TimeoutError as waited:  # no worker came free in time
            raise TooSlow() from waited
        finally:  # a wait given up is the clearest case of a busy server
            tally(Tally.QUEUED_MS, ms_since(waited_from))
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
        """Whether the pool takes tasks: replaced first if it broke, then its workers started.

        Busy workers still count: a task sent now waits for one to come free.
        """
        self._require_own_loop()
        try:
            return _is_active(await self._running())  # starts a new pool's workers
        except BrokenProcessPool:  # raised when a new pool can't start its workers
            return False

    async def _running(self) -> ProcessPool:
        """The pool, or a new one if a worker died between tasks and broke it."""
        async with self.replacing:
            if not _is_active(self.slot.pool):  # pebble stops taking tasks for good after that
                broken = self.slot.replace()
                broken.stop()
                await asyncio.to_thread(broken.join)  # waits for pebble's own threads
            return self.slot.pool

    async def _in_worker[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` on a worker set aside for it, killed by pebble at `deadline`.

        Sent once more, on a new pool, if the pool broke before a worker started it: it
        did no work, so nothing runs twice.
        """
        with contextlib.suppress(_NeverStarted):  # the pool broke before a worker started it
            return await self._sent(deadline, task)
        try:
            return await self._sent(deadline, task)
        except _NeverStarted as again:  # raised when the new pool broke the same way
            raise NoWorkers() from again

    async def _sent[T](self, deadline: float, task: Callable[[], T]) -> T:
        """`task()` sent to the pool, a new one if it broke, killed by pebble at `deadline`.

        Raises _NeverStarted if the pool breaks before a worker starts it; any other
        failure goes up as it is.
        """
        # Worked out before a broken pool is replaced: that's the server's time, not the task's.
        time_left = deadline - self.loop.time()
        if time_left <= 0:  # out of time already; pebble reads 0 as no timeout at all
            raise TooSlow()
        try:
            pool = await self._running()
        except BrokenProcessPool as broke:  # raised when no worker process or pipe can be had
            _log.write(LogEvent.NO_WORKER_STARTED, broke)
            raise
        start = self.starts / uuid.uuid4().hex  # this try's own: the worker makes it
        try:
            job = partial(_noted_start, str(start), request_id=current_request_id(), task=task)
            future = pool.submit(job, time_left)
        # Raised by pebble: the pool broke, or no worker or pipe could be had for a new one.
        except (RuntimeError, OSError) as refused:
            _log.write(LogEvent.POOL_REFUSED_TASK, refused)
            raise _NeverStarted() from refused
        # Only for a wait pebble never answers on an unbroken pool: its timeout comes first.
        backstop = asyncio.timeout(time_left + constants.WORKER_START_S)
        try:
            async with backstop:
                return await self._tallied_answer(pool, future)
        except TimeoutError:  # raised by pebble at the task's timeout, or by the backstop
            if backstop.expired():  # a fault of pebble's, for whoever runs the server
                _log.write(LogEvent.POOL_NEVER_ANSWERED)
            raise
        except BrokenProcessPool as broke:  # the pool broke: pebble says so, or lost the task
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
        """The task's result, with what it tallied in its worker added to the request's line."""
        try:
            answer = await self._answer_from(pool, future)
        except Exception as failure:  # the task's own, carrying its tallies, or pebble's
            add_from_worker(tallies_carried_by(failure))
            raise
        add_from_worker(answer.tallies)
        # Off the loop: it may hold a whole file.
        result = await asyncio.to_thread(_read_back, answer.result)
        return cast(T, result)  # pickled from `task()`, which returns a T

    async def _answer_from(
        self, pool: ProcessPool, future: ProcessFuture[WorkerAnswer[bytes]]
    ) -> WorkerAnswer[bytes]:
        """pebble's answer to a task sent to `pool`, watching that the pool doesn't lose it.

        Raises BrokenProcessPool if it does: pebble says nothing of a task sent as it gives up.
        """
        answer = asyncio.wrap_future(future)
        try:
            while pool.active:
                await asyncio.wait([answer], timeout=constants.BROKEN_POOL_CHECK_S)
                if answer.done():
                    return answer.result()
            # Its threads joined first, so nothing answers the task once it's looked at.
            await self._running()
            if not future.done():  # pebble lost it
                raise BrokenProcessPool("The worker pool broke and lost a task")
            return await answer
        finally:
            answer.cancel()  # a wait cut short cancels pebble's task too

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


def _noted_start[T](
    start: str, *, request_id: str | None, task: Callable[[], T]
) -> WorkerAnswer[bytes]:
    """In a worker: note that it started `task` by making the file `start`, then run it.

    Its lines carry the id of the request it's for, and what it tallied goes back
    with it. Its result is pickled here: pebble's threads then never read one of ours.
    """
    Path(start).touch()
    try:
        answer = in_request(request_id, partial(_run_tallied, task))
    except Exception as failure:  # the task's own, which pebble sends back as it is
        _require_trip(failure)
        raise
    return WorkerAnswer(_pickled(answer.result, tallies=answer.tallies), answer.tallies)


def _pickled(result: object, *, tallies: Tallies) -> bytes:
    """In a worker: `result` as bytes to send back, or _CantMakeTheTrip carrying `tallies`."""
    # Into a stream, not `pickle.dumps`, whose buffer grows past the result's size.
    sent_back = io.BytesIO()
    try:
        pickle.Pickler(sent_back, protocol=pickle.HIGHEST_PROTOCOL).dump(result)
    except MemoryError:  # past the worker's memory ceiling: too heavy, as any task is
        raise
    except Exception as problem:  # pickling can fail in many ways on an object
        unsent = _cant_make_the_trip(type(result).__name__, problem)
        raise carried(unsent, tallies) from problem
    return sent_back.getvalue()  # the stream's own buffer, not a copy


def _require_trip(failure: Exception) -> None:
    """In a worker: raise _CantMakeTheTrip in `failure`'s place unless it can make the trip."""
    try:
        pickle.loads(pickle.dumps(failure))  # our own bytes, made just now
    except MemoryError:  # past the worker's memory ceiling: too heavy, as any task is
        raise
    except Exception as problem:  # pickling or reading back can fail in many ways
        unsent = _cant_make_the_trip(type(failure).__name__, problem)
        raise carried(unsent, tallies_carried_by(failure)) from problem


def _cant_make_the_trip(kind: str, problem: Exception) -> _CantMakeTheTrip:
    """What kind of answer can't go back, and what stopped it: never the answer's own words."""
    return _CantMakeTheTrip(f"{kind} can't make the trip back: {type(problem).__name__}")


def _read_back(sent_back: bytes) -> object:
    """A task's result as it was, or _CantMakeTheTrip if it can't be read back."""
    try:
        return pickle.loads(sent_back)  # our own worker's, made by `_pickled`
    except Exception as problem:  # reading back can fail in many ways on an object
        raise _cant_make_the_trip("a task's result", problem) from problem


def _run_tallied[T](task: Callable[[], T]) -> T:
    """In a worker: run `task`, tallying its time and whether the worker was cold."""
    if _THIS_WORKER.warm_up():
        tally(Tally.COLD)
    began = time.monotonic()
    try:
        return result_of(task)
    finally:
        tally(Tally.WORKER_MS, ms_since(began))


def _is_active(pool: ProcessPool) -> bool:
    """Whether pebble's pool takes tasks, starting a new one first.

    Raises BrokenProcessPool when a new one can't start its pipes, threads or workers.
    """
    try:
        return pool.active
    # Raised by pebble starting a new pool: no files left for its pipes, no thread to start.
    except (OSError, RuntimeError) as cant_start:
        raise BrokenProcessPool("The worker pool couldn't start") from cant_start


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
        initializer=_start_worker,
        # Spawn: a fork of a threaded server can inherit a held lock and hang.
        # The cast because pebble types `context` as a module; it takes any.
        context=cast(ModuleType, multiprocessing.get_context("spawn")),
    )


def _log_unexpected[T](job: asyncio.Task[T]) -> None:
    """Log how a task nobody waits for now failed, unless the pool expects that failure.

    A server error is expected, but its debug is still logged.
    """
    if job.cancelled():
        return
    failure = job.exception()  # read here, so asyncio doesn't log it as never read
    if failure is None:
        return
    # The server's own failure: its debug is for the log, as the API's handler does.
    if _has_server_debug(failure):
        _log.write(LogEvent.TASK_FAILED_CALLER_LEFT, failure, level=WARN)
        return
    # The file's or the request's doing, or pebble's: its caller's answer, no news.
    if isinstance(failure, _EXPECTED_WHEN_LEFT):
        return
    _log.write(LogEvent.TASK_FAILED_CALLER_LEFT, failure)


def _has_server_debug(failure: BaseException) -> bool:
    """Whether a failure is a server error's Problem, with a debug for the log."""
    if not isinstance(failure, Problem):
        return False
    on_our_side = failure.status >= status.HTTP_500_INTERNAL_SERVER_ERROR
    return on_our_side and failure.debug is not None


def current(request: Request) -> WorkerPool:
    """The app's pool, started with it. A route's dependency."""
    return request.app.state.pool


def _start_worker() -> None:
    """Set a new worker up: its lines formatted as the server's, its memory capped."""
    LogController.configure()
    _limit_memory()


def _limit_memory() -> None:
    """Cap a worker's memory, so a hostile PDF runs out in its worker, not the server.

    Linux only. macOS doesn't enforce RLIMIT_AS, so development runs uncapped.
    """
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
