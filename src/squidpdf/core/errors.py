"""Every failure a person can be told about: its wire `type`, its status, its sentence.

Framework-free, so the CLI and the worker processes use it too. The API sends
one as Problem Details (`api/errors/http.py`); the CLI prints its `detail`. Each
feature keeps its own subclasses in its own `errors.py`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from squidpdf.core import words


class Problem(Exception):
    """Subclass it and set the three class attributes; `fill` fills the sentence.

    `debug` is the technical why, for a developer: sent beside `detail`, never in it.
    """

    type: ClassVar[str] = "server_error"  # what the browser branches on: never renamed
    status: ClassVar[int] = 500
    sentence: ClassVar[str] = words.SERVER_ERROR

    def __init__(self, debug: str | None = None, **fill: object) -> None:
        """Keep what the sentence needs, and the developer's why."""
        super().__init__(self.type)
        self.debug = debug
        self.fill = fill

    @property
    def detail(self) -> str:
        """The sentence the user reads, placeholders filled."""
        return self.sentence.format(**self.fill)

    def __reduce__(self) -> tuple[Any, ...]:
        """Raised in a worker, it reaches the server whole: `fill` isn't in `args`."""
        return _rebuild, (type(self), self.debug, self.fill)


def _rebuild(cls: type[Problem], debug: str | None, fill: dict[str, object]) -> Problem:
    """A pickled Problem as it was raised, bypassing the subclass's own arguments."""
    problem = cls.__new__(cls)
    Problem.__init__(problem, debug, **fill)
    return problem


class Unreadable(Problem):
    """The engine can't open the file. Catch this for both kinds."""

    type = "damaged"
    status = 422
    sentence = words.DAMAGED


class Encrypted(Unreadable):
    """It opened, but only a password would let us read it."""

    type = "encrypted"
    sentence = words.ENCRYPTED


class Damaged(Unreadable):
    """Garbage, truncated or empty; or MuPDF crashed on it."""
