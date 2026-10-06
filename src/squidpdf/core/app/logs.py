"""The one door to logging: each line says what happened, in one shape, and carries no document.

No other module imports `logging` (`tests/test_layers.py`). A field is never a
plain string, so a line can't carry a file's words; a traceback or a debug after
it has any document's folder hidden.
"""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
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

# The request the running code answers, if any: set by the API, read into every line.
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class Level(Enum):
    """How much a line matters, chosen by its caller: there's no default."""

    INFO = logging.INFO
    WARN = logging.WARNING
    ERROR = logging.ERROR


INFO, WARN, ERROR = Level.INFO, Level.WARN, Level.ERROR
# The level's name as a line writes it, by the stdlib's number.
_LEVEL_NAMES = {level.value: level.name for level in Level}


class LogEvent(StrEnum):
    """Everything the server can say in its log, each written as its value."""

    REQUEST_DONE = "request_done"
    UNHANDLED = "unhandled"
    PROBLEM_SENT_WITHOUT_DEBUG = "problem_sent_without_debug"
    TASK_FAILED_CALLER_LEFT = "task_failed_caller_left"
    NO_WORKER_STARTED = "no_worker_started"
    POOL_REFUSED_TASK = "pool_refused_task"
    POOL_NEVER_ANSWERED = "pool_never_answered"
    POOL_BROKE = "pool_broke"
    SWEEP_FAILED = "sweep_failed"
    GOOGLE_CACHE_MISSED = "google_cache_missed"
    GOOGLE_FETCH_LATE = "google_fetch_late"
    GOOGLE_FETCH_FAILED = "google_fetch_failed"
    GOOGLE_NOT_ON_GITHUB = "google_not_on_github"
    GOOGLE_WRONG_FILE = "google_wrong_file"
    GOOGLE_CUT_FAILED = "google_cut_failed"
    GOOGLE_CACHE_DAMAGED = "google_cache_damaged"
    GOOGLE_DAMAGED_KEPT = "google_damaged_kept"
    GOOGLE_NOT_CACHED = "google_not_cached"


class _Outcome(StrEnum):
    """What happened, the column a grep finds a line by, whatever its level."""

    NOTED = "noted"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OwnText:
    """Text the server wrote itself, never read from a file or a request: a route's template."""

    text: str


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
    """Where a module logs: one line per outcome, its level said by the caller."""

    logger: logging.Logger
    module: str  # the module's name under the package, as the line shows it

    @classmethod
    def for_module(cls, name: str) -> LogController:
        """The controller for module `name`, as `__name__` gives it."""
        return cls(logging.getLogger(name), module=name.removeprefix(f"{_ROOT}."))

    def noted(self, level: Level, event: LogEvent, **fields: Field) -> None:
        """Something happened as it should, worth a line."""
        self._log(_Outcome.NOTED, level, event, fields=fields)

    def skipped(self, level: Level, event: LogEvent, **fields: Field) -> None:
        """Something was left undone, and the work went on without it."""
        self._log(_Outcome.SKIPPED, level, event, fields=fields)

    def failed(
        self, level: Level, event: LogEvent, failure: BaseException, **fields: Field
    ) -> None:
        """Something went wrong; its traceback, and a Problem's debug, follow the line."""
        self._log(_Outcome.FAILED, level, event, fields=fields, failure=failure)

    def _log(
        self,
        outcome: _Outcome,
        level: Level,
        event: LogEvent,
        *,
        fields: dict[str, Field],
        failure: BaseException | None = None,
    ) -> None:
        """One line, its fields checked, with the request it's for; what failed after it."""
        words = [f"{outcome:<{_OUTCOME_WIDTH}}", self.module, event.value]
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
        self.logger.log(level.value, line)

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


def new_request_id() -> str:
    """Start a request: a random id its lines carry, which links to nothing but the log."""
    request_id = secrets.token_hex(_REQUEST_ID_BYTES)
    _REQUEST_ID.set(request_id)
    return request_id


def current_request_id() -> str | None:
    """The id of the request the running code answers; None outside a request."""
    return _REQUEST_ID.get()


def in_request[T](request_id: str | None, task: Callable[[], T]) -> T:
    """`task()`, its lines carrying `request_id`: for a worker, which misses its context."""
    _REQUEST_ID.set(request_id)
    return task()
