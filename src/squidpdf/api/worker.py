"""What runs in each worker process: its start, and each task's trip there and back.

Apart from `api/pool.py`, which imports the web framework: a worker imports this and its
jobs, never FastAPI, so a new one starts on what PDF work needs.
"""

from __future__ import annotations

import gc
import io
import pickle
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from squidpdf.api import constants
from squidpdf.core import (
    LogController,
    Tallies,
    Tally,
    WorkerAnswer,
    carried,
    in_request,
    ms_since,
    result_of,
    tallies_carried_by,
    tally,
)


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


class _CantMakeTheTrip(Exception):
    """A task's result or failure that can't be pickled or read back: our bug, not pebble's.

    Made by `_cant_make_the_trip`, with one message, so it makes the trip itself.
    """


def start_worker() -> None:
    """Set a new worker up: lines as the server's, memory capped, garbage collected in bulk."""
    LogController.configure()
    _limit_memory()
    # The youngest objects' alone: the older ones are collected every so many of its passes.
    gc.set_threshold(constants.WORKER_GC_THRESHOLD)


def _limit_memory() -> None:
    """Cap a worker's memory, so a hostile PDF runs out in its worker, not the server.

    Linux only. macOS doesn't enforce RLIMIT_AS, so development runs uncapped.
    """
    if sys.platform == "linux":
        import resource

        cap = constants.WORKER_MEMORY_BYTES
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def noted_start[T](
    start: Path, *, request_id: str | None, task: Callable[[], T]
) -> WorkerAnswer[bytes]:
    """In a worker: note that it started `task` by making the file `start`, then run it.

    Its lines carry the id of the request it's for, and what it tallied goes back
    with it. Its result is pickled here: pebble's threads then never read one of ours.
    """
    start.touch()
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


def read_back(sent_back: bytes) -> object:
    """In the server: a task's result as it was, or _CantMakeTheTrip if it won't read back."""
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
