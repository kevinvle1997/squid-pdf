from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from squidpdf.core.app import words
from squidpdf.core.app.message import Message, Param


class Problem(Exception):
    type: ClassVar[str] = "server_error"  # what the browser branches on: never renamed
    status: ClassVar[int] = 500

    def __init__(self, debug: str | None = None, **fill: Param) -> None:
        super().__init__(self.type)
        self.debug = debug
        self.fill = fill

    @property
    def message(self) -> Message:
        return Message(self.type, dict(self.fill))

    def sentence_in(self, language: str) -> str:
        return words.render(self.message, language)

    @property
    def detail(self) -> str:
        return self.sentence_in(words.ENGLISH)

    def __reduce__(self) -> tuple[Any, ...]:
        """Pickled whole from a worker: `fill` isn't in `args`."""
        return _rebuild, (type(self), self.debug, self.fill), self.__dict__


def _rebuild(cls: type[Problem], debug: str | None, fill: dict[str, Param]) -> Problem:
    problem = cls.__new__(cls)
    Problem.__init__(problem, debug, **fill)
    return problem


class NotFound(Problem):
    """Never 403: another's id looks unknown."""

    type = "not_found"
    status = 404


class InvalidRequest(Problem):
    type = "invalid_request"
    status = 400


class Damaged(Problem):
    type = "damaged"
    status = 422


class Encrypted(Problem):
    type = "encrypted"
    status = 422


class TooHeavy(Problem):
    type = "too_heavy"
    status = 422


@dataclass(frozen=True, slots=True)
class Failure:
    raised: type[Exception]
    problem: type[Problem] | Callable[[Exception], Problem]

    def problem_of(self, exc: Exception) -> Problem:
        if isinstance(self.problem, type):
            return self.problem(debug=_described(exc))
        return self.problem(exc)


@dataclass(frozen=True, slots=True)
class ErrorController:
    rows: tuple[Failure, ...]

    def with_rows(self, *rows: Failure) -> ErrorController:
        return ErrorController((*self.rows, *rows))

    def problem_of(self, exc: Exception) -> Problem:
        if isinstance(exc, Problem):
            return exc
        claimed = self.problem_if_claimed(exc)
        if claimed is None:
            return Problem(debug=_described(exc))
        return claimed

    def problem_if_claimed(self, exc: Exception) -> Problem | None:
        claiming = (row for row in self.rows if isinstance(exc, row.raised))
        row = next(claiming, None)
        if row is None:
            return None
        return row.problem_of(exc)

    def result_of[T](self, task: Callable[[], T]) -> T:
        """A library's exception may hold a pointer and not cross processes; its meaning can."""
        try:
            return task()
        except Problem:
            raise
        except Exception as exc:
            claimed = self.problem_if_claimed(exc)
            if claimed is None:
                raise
            raise claimed from exc


def _described(exc: Exception) -> str:
    text = str(exc)
    if not text:
        return type(exc).__name__
    return f"{type(exc).__name__}: {text}"


_OUT_OF_MEMORY = re.compile(
    r"\b(?:malloc|calloc|realloc)\b|\bout of memory\b|\bcannot allocate memory\b", re.IGNORECASE
)


def machine_failure(exc: Exception) -> Problem:
    """MuPDF raises the same error for both, so only its words tell them apart."""
    out_of_memory = _OUT_OF_MEMORY.search(str(exc)) is not None
    if out_of_memory:
        return TooHeavy(debug=_described(exc))
    return Problem(debug=_described(exc))
