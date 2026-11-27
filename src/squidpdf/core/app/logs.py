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

_ROOT = "squidpdf"
_REQUEST_ID_BYTES = 4  # 8 hex digits: enough to find one request's lines in a day's log
_OUTCOME_WIDTH = 7
_LEVEL_WIDTH = 5
_DOCUMENT_FOLDER = re.compile(r"(?<=[/\\])[A-Za-z0-9_-]{22}(?=[/\\'\"\s:]|$)", re.MULTILINE)
_HIDDEN = "<document>"
_MS_PER_S = 1000

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class _Level(Enum):
    INFO = logging.INFO
    WARN = logging.WARNING
    ERROR = logging.ERROR


INFO, WARN, ERROR = _Level.INFO, _Level.WARN, _Level.ERROR
_LEVEL_NAMES = {level.value: level.name for level in _Level}


class Tally(StrEnum):
    QUEUED_MS = "queued_ms"
    WORKER_MS = "worker_ms"
    COLD = "cold"
    REANALYSED = "reanalysed"
    GOOGLE_HIT = "google_hit"
    GOOGLE_FETCHED = "google_fetched"
    GOOGLE_FAILED = "google_failed"
    GOOGLE_MS = "google_ms"
    EDITS_EXACT = "edits_exact"
    EDITS_SUBSTITUTE = "edits_substitute"
    EDITS_APPROXIMATE = "edits_approximate"


class _Outcome(StrEnum):
    NOTED = "noted"
    SKIPPED = "skipped"
    FAILED = "failed"


_NOTED, _SKIPPED, _FAILED = _Outcome.NOTED, _Outcome.SKIPPED, _Outcome.FAILED


@dataclass(frozen=True, slots=True)
class _EventFacts:
    written: str
    outcome: _Outcome
    level: _Level


class LogEvent(Enum):
    REQUEST_DONE = _EventFacts("request_done", _NOTED, INFO)
    UNHANDLED = _EventFacts("unhandled", _FAILED, ERROR)
    PROBLEM_SENT_WITHOUT_DEBUG = _EventFacts("problem_sent_without_debug", _FAILED, WARN)
    TASK_FAILED_CALLER_LEFT = _EventFacts("task_failed_caller_left", _FAILED, ERROR)
    NO_WORKER_STARTED = _EventFacts("no_worker_started", _FAILED, WARN)
    POOL_REFUSED_TASK = _EventFacts("pool_refused_task", _FAILED, WARN)
    POOL_NEVER_ANSWERED = _EventFacts("pool_never_answered", _SKIPPED, WARN)
    POOL_BROKE = _EventFacts("pool_broke", _FAILED, WARN)
    SWEEP_FAILED = _EventFacts("sweep_failed", _FAILED, ERROR)
    FONT_LIST_FAILED = _EventFacts("font_list_failed", _FAILED, ERROR)
    GOOGLE_CACHE_MISSED = _EventFacts("google_cache_missed", _SKIPPED, INFO)
    GOOGLE_FETCH_LATE = _EventFacts("google_fetch_late", _SKIPPED, WARN)
    GOOGLE_FETCH_FAILED = _EventFacts("google_fetch_failed", _FAILED, WARN)
    GOOGLE_NOT_ON_GITHUB = _EventFacts("google_not_on_github", _FAILED, WARN)
    GOOGLE_WRONG_FILE = _EventFacts("google_wrong_file", _SKIPPED, WARN)
    GOOGLE_CUT_FAILED = _EventFacts("google_cut_failed", _FAILED, WARN)
    GOOGLE_CACHE_DAMAGED = _EventFacts("google_cache_damaged", _SKIPPED, WARN)
    GOOGLE_DAMAGED_KEPT = _EventFacts("google_damaged_kept", _FAILED, WARN)
    GOOGLE_NOT_CACHED = _EventFacts("google_not_cached", _FAILED, WARN)
    EDIT_IN_SUBSTITUTE = _EventFacts("edit_in_substitute", _NOTED, INFO)
    STRIPE_POSTED = _EventFacts("stripe_posted", _NOTED, INFO)
    STRIPE_CANCELLED = _EventFacts("stripe_cancelled", _NOTED, WARN)
    STRIPE_RETRIED = _EventFacts("stripe_retried", _SKIPPED, WARN)
    STRIPE_SIGNATURE_REFUSED = _EventFacts("stripe_signature_refused", _SKIPPED, WARN)
    BILLING_EVENT_NOTED = _EventFacts("billing_event_noted", _NOTED, INFO)
    BILLING_EVENT_REPEATED = _EventFacts("billing_event_repeated", _SKIPPED, INFO)
    BILLING_CUSTOMER_UNKNOWN = _EventFacts("billing_customer_unknown", _SKIPPED, WARN)
    BILLING_SUBSCRIBED_TWICE = _EventFacts("billing_subscribed_twice", _NOTED, ERROR)
    BILLING_DISPUTED = _EventFacts("billing_disputed", _NOTED, WARN)
    LINK_NOT_SENT_UNSAID = _EventFacts("link_not_sent_unsaid", _FAILED, WARN)
    MAIL_BOUNCE_NOTED = _EventFacts("mail_bounce_noted", _NOTED, INFO)
    MAIL_TO_BOUNCED_SKIPPED = _EventFacts("mail_to_bounced_skipped", _SKIPPED, INFO)


@dataclass(frozen=True, slots=True)
class OwnText:
    text: str


@dataclass(slots=True)
class Tallies:
    amounts: dict[Tally, int] = field(default_factory=dict)

    def add(self, tally: Tally, amount: int) -> None:
        self.amounts[tally] = self.amounts.get(tally, 0) + amount

    def add_all(self, more: Tallies) -> None:
        for tally, amount in more.amounts.items():
            self.add(tally, amount)


@dataclass(frozen=True, slots=True)
class WorkerAnswer[T]:
    result: T
    tallies: Tallies


_TALLIES: contextvars.ContextVar[Tallies | None] = contextvars.ContextVar(
    "tallies", default=None
)
_CARRIED = "squidpdf_tallies"


type _Field = int | float | bool | Enum | OwnText
# Looked up along the value's ancestry, so a bool is written as one before it's an int.
_WRITTEN: dict[type, Callable[[Any], str]] = {
    bool: lambda yes: "true" if yes else "false",
    int: str,
    float: str,
    Enum: lambda ours: str(ours.value),
    OwnText: lambda own: own.text,
}


@dataclass(frozen=True, slots=True, eq=False)
class LogController:
    logger: logging.Logger
    module: str

    @classmethod
    def for_module(cls, name: str) -> LogController:
        return cls(logging.getLogger(name), module=name.removeprefix(f"{_ROOT}."))

    def write(
        self,
        event: LogEvent,
        failure: BaseException | None = None,
        /,
        *,
        level: _Level | None = None,
        **fields: _Field,
    ) -> None:
        facts = event.value
        if (failure is not None) != (facts.outcome is _FAILED):
            raise TypeError(f"{facts.written}: what failed goes with a failed event, and only")
        words = [f"{facts.outcome:<{_OUTCOME_WIDTH}}", self.module, facts.written]
        words += [f"{key}={_written(value)}" for key, value in fields.items()]
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
        root = logging.getLogger(_ROOT)
        root.setLevel(logging.INFO)
        if not any(isinstance(handler, _Lines) for handler in root.handlers):
            root.addHandler(_Lines())


def _written(value: _Field) -> str:
    ancestry = type(value).__mro__
    write = next((_WRITTEN[kind] for kind in ancestry if kind in _WRITTEN), None)
    # A str may be a file's words: one of ours comes as an enum or OwnText.
    if write is None:
        raise TypeError(f"a log field can't be a {type(value).__name__}")
    return write(value)


def _what_failed(failure: BaseException) -> str:
    traceback_text = "".join(traceback.format_exception(failure)).rstrip("\n")
    debug = failure.debug if isinstance(failure, Problem) else None
    if debug is None:
        return traceback_text
    return f"debug: {debug}\n{traceback_text}"


class _Lines(logging.Handler):
    def format(self, record: logging.LogRecord) -> str:
        when = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = _LEVEL_NAMES[record.levelno]
        return f"{when} {level:<{_LEVEL_WIDTH}} {record.getMessage()}"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 (a closed or full stdout fails in many ways)
            self.handleError(record)


def start_request() -> str:
    request_id = secrets.token_hex(_REQUEST_ID_BYTES)
    _REQUEST_ID.set(request_id)
    _TALLIES.set(Tallies())
    return request_id


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def tally(kind: Tally, amount: int = 1) -> None:
    running = _TALLIES.get()
    if running is not None:
        running.add(kind, amount)


def tally_fields() -> dict[str, int]:
    running = _TALLIES.get()
    amounts = {} if running is None else running.amounts
    return {tally.value: amounts[tally] for tally in Tally if tally in amounts}


def in_request[T](request_id: str | None, task: Callable[[], T]) -> WorkerAnswer[T]:
    """A worker misses the request's context: its tallies go back with the result or failure."""
    _REQUEST_ID.set(request_id)
    tallies = Tallies()
    _TALLIES.set(tallies)
    try:
        return WorkerAnswer(task(), tallies)
    except Exception as failure:  # the task's: a failed request is often the slow one
        carried(failure, tallies)
        raise


def add_from_worker(tallies: Tallies | None) -> None:
    running = _TALLIES.get()
    if tallies is not None and running is not None:
        running.add_all(tallies)


def carried[E: BaseException](failure: E, tallies: Tallies | None) -> E:
    with contextlib.suppress(AttributeError):
        setattr(failure, _CARRIED, tallies)
    return failure


def tallies_carried_by(failure: BaseException) -> Tallies | None:
    return getattr(failure, _CARRIED, None)


def ms_since(started: float) -> int:
    return round((time.monotonic() - started) * _MS_PER_S)
