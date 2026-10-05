"""The one door to logging: each line says what happened, in one shape, and carries no document.

No other module imports `logging` (`tests/test_layers.py`). A field is never a
plain string, so a line can't carry a file's words; a traceback or a debug after
it has any document's folder hidden.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import secrets
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from squidpdf.core.app.errors import Problem

_ROOT = "squidpdf"  # every module's logger is under it, by its own name
_REQUEST_ID_BYTES = 4  # 8 hex digits: enough to find one request's lines in a day's log
_OUTCOME_WIDTH = 7  # "skipped", the longest, so the column after it lines up
_LEVEL_WIDTH = 5  # "ERROR", the longest
# A document's folder in a path, named as the documents store names it (`token_urlsafe`):
# a document's id, which a library's error or a server error's debug can name.
_DOCUMENT_FOLDER = re.compile(r"(?<=[/\\])[A-Za-z0-9_-]{22}(?=[/\\'\"\s:]|$)", re.MULTILINE)
_HIDDEN = "<document>"  # what stands in for one in the log
_MS_PER_S = 1000

# The request the running code answers, if any: set by the API, read into every line.
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class Level(Enum):
    """How much a line matters: its event's usual level, unless the call says otherwise."""

    INFO = logging.INFO
    WARN = logging.WARNING
    ERROR = logging.ERROR


INFO, WARN, ERROR = Level.INFO, Level.WARN, Level.ERROR
# The level's name as a line writes it, by the stdlib's number.
_LEVEL_NAMES = {level.value: level.name for level in Level}


class Tally(StrEnum):
    """What a request did on the way, tallied onto its line: why a slow one was slow."""

    QUEUED_MS = "queued_ms"  # waiting for a free worker
    WORKER_MS = "worker_ms"  # at work in one
    COLD = "cold"  # tasks that were a new worker's first, which fills every cache
    REANALYSED = "reanalysed"  # analyses worked out again, after a deploy retired them
    GOOGLE_HIT = "google_hit"  # Google's copies read from the disk cache
    GOOGLE_FETCHED = "google_fetched"  # downloaded in time
    GOOGLE_FAILED = (
        "google_failed"  # none came: failed, late, or held back after a recent failure
    )
    GOOGLE_MS = "google_ms"  # waiting on downloads


class _Outcome(StrEnum):
    """What happened, the column a grep finds a line by, whatever its level."""

    NOTED = "noted"  # something happened as it should, worth a line
    SKIPPED = "skipped"  # something was left undone, and the work went on without it
    FAILED = "failed"  # something went wrong: its traceback follows the line


NOTED, SKIPPED, FAILED = _Outcome.NOTED, _Outcome.SKIPPED, _Outcome.FAILED


@dataclass(frozen=True, slots=True)
class _EventFacts:
    """An event's word in the line, what happened, and how much it usually matters."""

    written: str
    outcome: _Outcome
    level: Level  # a call can say this one matters more, or less


class LogEvent(Enum):
    """Everything the server can say in its log: its word, what happened, its usual level.

    One list, so an event always has the same outcome, and a grep finds it at one level
    unless a call says this one matters more or less.
    """

    REQUEST_DONE = _EventFacts("request_done", NOTED, INFO)
    UNHANDLED = _EventFacts("unhandled", FAILED, ERROR)
    PROBLEM_SENT_WITHOUT_DEBUG = _EventFacts("problem_sent_without_debug", FAILED, WARN)
    TASK_FAILED_CALLER_LEFT = _EventFacts("task_failed_caller_left", FAILED, ERROR)
    NO_WORKER_STARTED = _EventFacts("no_worker_started", FAILED, WARN)
    POOL_REFUSED_TASK = _EventFacts("pool_refused_task", FAILED, WARN)
    POOL_NEVER_ANSWERED = _EventFacts("pool_never_answered", SKIPPED, WARN)
    POOL_BROKE = _EventFacts("pool_broke", FAILED, WARN)
    SWEEP_FAILED = _EventFacts("sweep_failed", FAILED, ERROR)
    GOOGLE_CACHE_MISSED = _EventFacts("google_cache_missed", SKIPPED, INFO)
    GOOGLE_FETCH_LATE = _EventFacts("google_fetch_late", SKIPPED, WARN)
    GOOGLE_FETCH_FAILED = _EventFacts("google_fetch_failed", FAILED, WARN)
    GOOGLE_NOT_ON_GITHUB = _EventFacts("google_not_on_github", FAILED, WARN)
    GOOGLE_WRONG_FILE = _EventFacts("google_wrong_file", SKIPPED, WARN)
    GOOGLE_CUT_FAILED = _EventFacts("google_cut_failed", FAILED, WARN)
    GOOGLE_CACHE_DAMAGED = _EventFacts("google_cache_damaged", SKIPPED, WARN)
    GOOGLE_DAMAGED_KEPT = _EventFacts("google_damaged_kept", FAILED, WARN)
    GOOGLE_NOT_CACHED = _EventFacts("google_not_cached", FAILED, WARN)


@dataclass(frozen=True, slots=True)
class OwnText:
    """Text the server wrote itself, never read from a file or a request: a route's template."""

    text: str


@dataclass(slots=True)
class Tallies:
    """What one request, or a task of it in a worker, tallied on the way."""

    amounts: dict[Tally, int] = field(default_factory=dict)

    def add(self, tally: Tally, amount: int) -> None:
        """Add `amount` to `tally`."""
        self.amounts[tally] = self.amounts.get(tally, 0) + amount  # .get: its first

    def add_all(self, more: Tallies) -> None:
        """Add everything `more` tallied."""
        for tally, amount in more.amounts.items():
            self.add(tally, amount)


@dataclass(frozen=True, slots=True)
class WorkerAnswer[T]:
    """A task's result, and what it tallied in its worker, for its request's line."""

    result: T
    tallies: Tallies


# What the request the running code answers has tallied; None outside a request.
_TALLIES: contextvars.ContextVar[Tallies | None] = contextvars.ContextVar(
    "tallies", default=None
)
# The attribute a task's failure carries its worker's tallies back to the server in.
_CARRIED = "squidpdf_tallies"


type Field = int | float | bool | Enum | OwnText
# How a field of each type is written. Looked up along the value's ancestry, so a bool is
# written as one before it's an int, and an enum of strings as an enum; a plain str has none.
_WRITTEN: dict[type, Callable[[Any], str]] = {
    bool: lambda yes: "true" if yes else "false",
    int: str,
    float: str,
    Enum: lambda ours: str(ours.value),  # ours, so its value is ours too
    OwnText: lambda own: own.text,
}


@dataclass(frozen=True, slots=True, eq=False)
class LogController:
    """Where a module logs: one line per event, at its usual level unless the call says."""

    logger: logging.Logger
    module: str  # the module's name under the package, as the line shows it

    @classmethod
    def for_module(cls, name: str) -> LogController:
        """The controller for module `name`, as `__name__` gives it."""
        return cls(logging.getLogger(name), module=name.removeprefix(f"{_ROOT}."))

    def write(
        self,
        event: LogEvent,
        failure: BaseException | None = None,
        /,
        *,
        level: Level | None = None,
        **fields: Field,
    ) -> None:
        """One line for `event`, its fields checked, with the request it's for.

        A failed event takes what failed, whose traceback and a Problem's debug
        follow the line; any other takes none. Raises TypeError when that's wrong.
        """
        facts = event.value
        if (failure is not None) != (facts.outcome is FAILED):
            raise TypeError(f"{facts.written}: what failed goes with a failed event, and only")
        words = [f"{facts.outcome:<{_OUTCOME_WIDTH}}", self.module, facts.written]
        words += [f"{key}={_written(value)}" for key, value in fields.items()]
        # A Problem's type is ours, and names what went wrong as the browser was told.
        if isinstance(failure, Problem):
            words.append(f"problem={failure.type}")
        request_id = _REQUEST_ID.get()
        if request_id is not None:
            words.append(f"request={request_id}")
        line = " ".join(words)
        if failure is not None:
            line += "\n" + _DOCUMENT_FOLDER.sub(_HIDDEN, _what_failed(failure))
        self.logger.log((level or facts.level).value, line)

    @staticmethod
    def configure() -> None:
        """Lines at INFO and above go to stdout, once however often it's called."""
        root = logging.getLogger(_ROOT)
        root.setLevel(logging.INFO)
        if not any(isinstance(handler, _Lines) for handler in root.handlers):
            root.addHandler(_Lines())


def _written(value: Field) -> str:
    """A field's value as the line writes it. Raises TypeError for anything but a Field."""
    ancestry = type(value).__mro__
    write = next((_WRITTEN[kind] for kind in ancestry if kind in _WRITTEN), None)
    # A str may be a file's words: one of ours comes as an enum or OwnText.
    if write is None:
        raise TypeError(f"a log field can't be a {type(value).__name__}")
    return write(value)


def _what_failed(failure: BaseException) -> str:
    """A failure's traceback, after a Problem's debug, which only the log is told."""
    traceback_text = "".join(traceback.format_exception(failure)).rstrip("\n")
    # A server error's why, kept from the browser, is for the log (`decisions/api.md`).
    debug = failure.debug if isinstance(failure, Problem) else None
    if debug is None:
        return traceback_text
    return f"debug: {debug}\n{traceback_text}"


class _Lines(logging.Handler):
    """Writes each line to stdout, after its time (UTC) and level, as one write.

    Stdout as it is when the line is written, so a test capturing it sees the line.
    """

    def format(self, record: logging.LogRecord) -> str:
        """The record as the controller wrote it, after the time and level."""
        when = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = _LEVEL_NAMES[record.levelno]
        return f"{when} {level:<{_LEVEL_WIDTH}} {record.getMessage()}"

    def emit(self, record: logging.LogRecord) -> None:
        """Write the record, whole."""
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 (a closed or full stdout fails in many ways)
            self.handleError(record)


def start_request() -> str:
    """Start a request, nothing tallied yet: a random id its lines carry, linked to nothing."""
    request_id = secrets.token_hex(_REQUEST_ID_BYTES)
    _REQUEST_ID.set(request_id)
    _TALLIES.set(Tallies())
    return request_id


def current_request_id() -> str | None:
    """The id of the request the running code answers; None outside a request."""
    return _REQUEST_ID.get()


def tally(kind: Tally, amount: int = 1) -> None:
    """Add to `kind` on the running request's line; outside a request, nothing."""
    running = _TALLIES.get()
    if running is not None:
        running.add(kind, amount)


def tally_fields() -> dict[str, int]:
    """What the running request tallied, as its line's fields, in the order `Tally` lists."""
    running = _TALLIES.get()
    amounts = {} if running is None else running.amounts
    return {tally.value: amounts[tally] for tally in Tally if tally in amounts}


def in_request[T](request_id: str | None, task: Callable[[], T]) -> WorkerAnswer[T]:
    """In a worker: `task()`, its lines carrying `request_id`, and what it tallied.

    A worker misses the request's context, so its tallies go back with the result,
    or on the failure it raises (`tallies_carried_by`), for the server to add.
    """
    _REQUEST_ID.set(request_id)
    tallies = Tallies()
    _TALLIES.set(tallies)
    try:
        return WorkerAnswer(task(), tallies)
    except Exception as failure:  # the task's: a failed request is often the slow one
        # One that refuses new attributes goes up as it is, carrying none.
        with contextlib.suppress(AttributeError):
            setattr(failure, _CARRIED, tallies)
        raise


def add_from_worker(tallies: Tallies | None) -> None:
    """Add what a task tallied in its worker to the running request's line."""
    running = _TALLIES.get()
    if tallies is not None and running is not None:
        running.add_all(tallies)


def tallies_carried_by(failure: BaseException) -> Tallies | None:
    """What a task tallied before it failed; None for pebble's own, as at a timeout."""
    return getattr(failure, _CARRIED, None)  # set by `in_request`, on the task's failures


def ms_since(started: float) -> int:
    """Whole milliseconds since `started`, a `time.monotonic()`."""
    return round((time.monotonic() - started) * _MS_PER_S)
