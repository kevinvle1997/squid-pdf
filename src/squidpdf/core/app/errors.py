"""Every failure a person can be told about: its wire `type`, its status, its sentence.

Framework-free, so the CLI and the worker processes use it too. The API sends
one as Problem Details (`api/errors/http.py`); the CLI prints its `detail`. Each
feature keeps its own subclasses in its own `errors.py`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from squidpdf.core.app import words
from squidpdf.core.app.message import Message, Param


class Problem(Exception):
    """Subclass it and set `type` and `status`; `fill` fills the sentence.

    A Message like any other: `type` is its sentence's key in `core.app.words`, and
    `fill` holds the facts. `debug` is the technical why, for a developer, never in
    `detail`; the API sends it on a 4xx and logs it on a 5xx.
    """

    type: ClassVar[str] = "server_error"  # what the browser branches on: never renamed
    status: ClassVar[int] = 500

    def __init__(self, debug: str | None = None, **fill: Param) -> None:
        """Keep what the sentence needs, and the developer's why."""
        super().__init__(self.type)
        self.debug = debug
        self.fill = fill

    @property
    def message(self) -> Message:
        """What the user is told, in no language yet: the sentence's key and its facts."""
        return Message(self.type, dict(self.fill))

    def said_in(self, language: str) -> str:
        """The sentence the user reads, in `language`: its Message, said as any other is."""
        return words.render(self.message, language)

    @property
    def detail(self) -> str:
        """The sentence the user reads, in English, placeholders filled."""
        return self.said_in(words.ENGLISH)

    def __reduce__(self) -> tuple[Any, ...]:
        """Raised in a worker, it reaches the server whole: `fill` isn't in `args`."""
        return _rebuild, (type(self), self.debug, self.fill)


def _rebuild(cls: type[Problem], debug: str | None, fill: dict[str, Param]) -> Problem:
    """A pickled Problem as it was raised, bypassing the subclass's own arguments."""
    problem = cls.__new__(cls)
    Problem.__init__(problem, debug, **fill)
    return problem


class NotFound(Problem):
    """Missing, expired, or someone else's. Never 403: another's id looks unknown."""

    type = "not_found"
    status = 404


class InvalidRequest(Problem):
    """A browser bug: a plain sentence for the user, the particulars in `debug`."""

    type = "invalid_request"
    status = 400


class Damaged(Problem):
    """The engine can't open the file: garbage, truncated or empty; or MuPDF crashed on it."""

    type = "damaged"
    status = 422


class Encrypted(Problem):
    """The engine can't read the file: it opened, but only a password would let us read it."""

    type = "encrypted"
    status = 422


# The engine can't open or read the file, whichever the reason: catch this for both.
Unreadable = (Damaged, Encrypted)


class TooHeavy(Problem):
    """The work went past the memory ceiling, or past a limit the PDF library keeps."""

    type = "too_heavy"
    status = 422


@dataclass(frozen=True, slots=True)
class Failure:
    """One way a library fails, and the Problem it means: one row of a failure table.

    `problem` is a Problem class, made with the exception's text in `debug`, or
    a function of the exception, for a row that must read it to say which.
    """

    raised: type[Exception]  # what the library raises, or a subclass of it
    problem: type[Problem] | Callable[[Exception], Problem]

    def problem_of(self, exc: Exception) -> Problem:
        """The Problem `exc`, which this row claims, means."""
        if isinstance(self.problem, type):  # a class: the exception says nothing more
            return self.problem(debug=_described(exc))
        return self.problem(exc)


@dataclass(frozen=True, slots=True)
class ErrorController:
    """The one place a failure becomes the Problem it means: one row per foreign exception.

    Rows are asked in order, and the first whose exception matches makes the
    Problem. A layer above adds its own library's rows with `with_rows`, after
    the ones below it.
    """

    rows: tuple[Failure, ...]

    def with_rows(self, *rows: Failure) -> ErrorController:
        """A controller that asks this one's rows first, then `rows`."""
        return ErrorController((*self.rows, *rows))

    def problem_of(self, exc: Exception) -> Problem:
        """`exc` as the Problem it means; a server error, with its text, if no row claims it."""
        if isinstance(exc, Problem):  # ours already: it means what it says
            return exc
        claimed = self.problem_if_claimed(exc)
        if claimed is None:  # a bug: nothing here knows what it means
            return Problem(debug=_described(exc))
        return claimed

    def problem_if_claimed(self, exc: Exception) -> Problem | None:
        """The Problem the first row that claims `exc` makes of it; None when no row does."""
        claiming = (row for row in self.rows if isinstance(exc, row.raised))
        row = next(claiming, None)  # None: no row claims it
        if row is None:
            return None
        return row.problem_of(exc)

    def result_of[T](self, task: Callable[[], T]) -> T:
        """What `task()` returns, or the Problem a row says its failure means.

        A worker runs its task through this: a library's exception may hold a
        pointer, so it can't be sent back from another process, but its meaning
        can. A failure no row claims goes up as it is, for a layer above to
        claim or to log as the bug it is.
        """
        try:
            return task()
        except Problem:  # ours already: it crosses as it is
            raise
        except Exception as exc:  # whatever else the task raised: a row may say what it means
            claimed = self.problem_if_claimed(exc)
            if claimed is None:  # no row here claims it
                raise
            raise claimed from exc


def _described(exc: Exception) -> str:
    """An exception as a developer reads it: its type and its text, if it has any."""
    text = str(exc)
    # A timeout, say, has no words of its own.
    if not text:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"


# How a C library words a failed allocation, e.g. MuPDF's "malloc (468750000 bytes) failed",
# or the system's own "Cannot allocate memory".
_OUT_OF_MEMORY = re.compile(
    r"\b(?:malloc|calloc|realloc)\b|\bout of memory\b|\bcannot allocate memory\b", re.IGNORECASE
)


def machine_failure(exc: Exception) -> Problem:
    """The machine failed, not the file: out of memory is too heavy, anything else ours.

    MuPDF raises the same error for both, so only its words tell them apart.
    A file it couldn't open, such as one deleted mid-export, is a server error.
    """
    out_of_memory = _OUT_OF_MEMORY.search(str(exc)) is not None
    if out_of_memory:
        return TooHeavy(debug=_described(exc))
    return Problem(debug=_described(exc))
