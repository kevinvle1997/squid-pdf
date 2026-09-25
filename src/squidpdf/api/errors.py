"""Every failure the API reports, as RFC 9457 Problem Details.

`type` is what the browser branches on and `detail` is shown to the user
verbatim, so it comes from `core/words.py` in plain words. The browser prevents
what it can: by the time the server says no, it's a backstop, never news.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from squidpdf.core import words

type ProblemType = Literal[
    "not_a_pdf",
    "too_large",
    "too_many_pages",
    "encrypted",
    "damaged",
    "not_found",
    "redaction_conflict",
    "bad_reference",
    "redaction_failed",
    "font_mismatch",
    "invalid_request",
    "rate_limited",
    "server_error",
]

# type -> status, and the sentence the user sees.
_PROBLEMS: dict[ProblemType, tuple[int, str]] = {
    "not_a_pdf": (415, words.NOT_A_PDF),
    "too_large": (413, words.TOO_LARGE),
    "too_many_pages": (422, words.TOO_MANY_PAGES),
    "encrypted": (422, words.ENCRYPTED),
    "damaged": (422, words.DAMAGED),
    "not_found": (404, words.NOT_FOUND),  # never 403: someone else's id looks unknown
    "redaction_conflict": (422, words.REDACTION_CONFLICT),
    "bad_reference": (422, words.BAD_REFERENCE),
    "redaction_failed": (422, words.REDACTION_FAILED),
    "font_mismatch": (422, words.FONT_MISMATCH),
    "invalid_request": (400, words.INVALID_REQUEST),
    "rate_limited": (429, words.RATE_LIMITED),
    "server_error": (500, words.SERVER_ERROR),
}

_MEDIA_TYPE = "application/problem+json"
_NOT_FOUND_STATUS = 404


class Problem(Exception):
    """Raise anywhere a request is handled and the browser gets Problem Details.

    `fill` fills the sentence's placeholders, e.g. `Problem("too_large", mb=100)`.
    """

    def __init__(self, type: ProblemType, **fill: object) -> None:
        """Look up the status and word the sentence for this `type`."""
        super().__init__(type)
        self.type = type
        self.status, sentence = _PROBLEMS[type]
        self.detail = sentence.format(**fill)


def _respond(problem: Problem) -> JSONResponse:
    """The Problem Details body, with its own media type."""
    return JSONResponse(
        {"type": problem.type, "status": problem.status, "detail": problem.detail},
        status_code=problem.status,
        media_type=_MEDIA_TYPE,
    )


async def _problem(request: Request, exc: Problem) -> Response:
    """A Problem a route raised on purpose."""
    return _respond(exc)


async def _invalid(request: Request, exc: RequestValidationError) -> Response:
    """Replaces FastAPI's validation body, a list of jargon, with one line.

    Only the browser sends requests, so this is a browser bug; the line is for
    whoever reads the report.
    """
    lines = []
    for error in exc.errors():
        loc = error["loc"]
        where = ".".join(str(part) for part in loc[1:]) or loc[0]  # drop "body", "query"
        lines.append(f"{where}: {error['msg']}")
    return _respond(Problem("invalid_request", problem="; ".join(lines)))


async def _http(request: Request, exc: HTTPException) -> Response:
    """Starlette's own: an unknown path is not_found, anything else a bad request."""
    if exc.status_code == _NOT_FOUND_STATUS:
        return _respond(Problem("not_found"))
    return _respond(Problem("invalid_request", problem=exc.detail))


async def _crash(request: Request, exc: Exception) -> Response:
    """Anything unhandled. Starlette logs the traceback after this has answered."""
    return _respond(Problem("server_error"))


# Passed to FastAPI whole, so every error the app can raise leaves as Problem Details.
HANDLERS: dict[
    int | type[Exception], Callable[[Request, Any], Coroutine[Any, Any, Response]]
] = {
    Problem: _problem,
    RequestValidationError: _invalid,
    HTTPException: _http,
    Exception: _crash,
}
