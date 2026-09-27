"""Every failure a person can be told about: its wire `type`, its status, its sentence.

Framework-free, so the CLI and the worker processes use it too. The API sends
one as Problem Details (`api/errors/http.py`); the CLI prints its `detail`. Each
feature keeps its own subclasses in its own `errors.py`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from squidpdf.core import words
from squidpdf.core.message import Message, Param

__all__ = [
    "Problem",
    "NotFound",
    "Unreadable",
    "Encrypted",
    "Damaged",
]


class Problem(Exception):
    """Subclass it and set `type` and `status`; `fill` fills the sentence.

    A Message like any other: `type` is its sentence's key in `core.words`, and
    `fill` holds the facts. `debug` is the technical why, for a developer: sent
    beside `detail`, never in it.
    """

    type: ClassVar[str] = "server_error"  # what the browser branches on: never renamed
    status: ClassVar[int] = 500
    # Its own sentence, only for a type no catalog has: the catalogs say the rest.
    sentence: ClassVar[str | None] = None

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
        """The sentence the user reads, in `language`, placeholders filled."""
        template = words.sentence(self.type, language, default=self.sentence)
        return words.fill(template, self.fill, language)

    @property
    def detail(self) -> str:
        """The sentence the user reads, in English, placeholders filled."""
        return self.said_in(words.ENGLISH)

    def __reduce__(self) -> tuple[Any, ...]:
        """Raised in a worker, it reaches the server whole: `fill` isn't in `args`."""
        return rebuild, (type(self), self.debug, self.fill)


def rebuild(cls: type[Problem], debug: str | None, fill: dict[str, Param]) -> Problem:
    """A pickled Problem as it was raised, bypassing the subclass's own arguments."""
    problem = cls.__new__(cls)
    Problem.__init__(problem, debug, **fill)
    return problem


class NotFound(Problem):
    """Missing, expired, or someone else's. Never 403: another's id looks unknown."""

    type = "not_found"
    status = 404


class Unreadable(Problem):
    """The engine can't open the file. Catch this for both kinds."""

    type = "damaged"
    status = 422


class Encrypted(Unreadable):
    """It opened, but only a password would let us read it."""

    type = "encrypted"


class Damaged(Unreadable):
    """Garbage, truncated or empty; or MuPDF crashed on it."""
