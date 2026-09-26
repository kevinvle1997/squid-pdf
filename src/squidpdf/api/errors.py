"""Every failure the API reports, as RFC 9457 Problem Details.

`type` is what the browser branches on and `detail` is shown to the user
verbatim, so it comes from `core/words.py` in plain words. The browser prevents
what it can: by the time the server says no, it's a backstop, never news.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException

from squidpdf.core import Unreadable, words


class Problem(Enum):
    """Every failure the browser can branch on: its status, then the sentence it shows.

    The wire `type` is the name in lower case, e.g. `not_found`.
    """

    NOT_A_PDF = 415, words.NOT_A_PDF
    TOO_LARGE = 413, words.TOO_LARGE
    TOO_MANY_PAGES = 422, words.TOO_MANY_PAGES
    ENCRYPTED = 422, words.ENCRYPTED
    DAMAGED = 422, words.DAMAGED
    NOT_FOUND = 404, words.NOT_FOUND  # never 403: someone else's id looks unknown
    REDACTION_CONFLICT = 422, words.REDACTION_CONFLICT
    BAD_REFERENCE = 422, words.BAD_REFERENCE
    REDACTION_FAILED = 422, words.REDACTION_FAILED
    FONT_MISMATCH = 422, words.FONT_MISMATCH
    INVALID_REQUEST = 400, words.INVALID_REQUEST
    RATE_LIMITED = 429, words.RATE_LIMITED
    SERVER_ERROR = 500, words.SERVER_ERROR


class ApiError(Exception):
    """Raise anywhere a request is handled and the browser gets Problem Details.

    `fill` fills the sentence's placeholders, e.g. `ApiError(Problem.TOO_LARGE, mb=100)`.
    """

    def __init__(self, problem: Problem, **fill: object) -> None:
        """Word the sentence for this problem."""
        super().__init__(problem.name)
        self.type = problem.name.lower()
        self.status, sentence = problem.value
        self.detail = sentence.format(**fill)


async def _handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets."""
    match exc:
        case ApiError():
            error = exc
        case Unreadable(encrypted=True):
            error = ApiError(Problem.ENCRYPTED)  # from a worker, as the file opened
        case Unreadable():
            error = ApiError(Problem.DAMAGED)
        case RequestValidationError():
            # One plain line, not FastAPI's jargon list: it's a browser bug report.
            reason = "; ".join(_describe(item) for item in exc.errors())
            error = ApiError(Problem.INVALID_REQUEST, reason=reason)
        case HTTPException(status_code=status.HTTP_404_NOT_FOUND):
            error = ApiError(Problem.NOT_FOUND)  # Starlette's own, for an unknown path
        case HTTPException():
            error = ApiError(Problem.INVALID_REQUEST, reason=exc.detail)
        case _:
            error = ApiError(Problem.SERVER_ERROR)  # Starlette logs the traceback after
    return JSONResponse(
        {"type": error.type, "status": error.status, "detail": error.detail},
        status_code=error.status,
        media_type="application/problem+json",
    )


def _describe(item: dict[str, Any]) -> str:
    """One validation failure as `field.path: message`."""
    # The part of the request, e.g. "body", then the path to the field inside it.
    part, *field_path = item["loc"]
    where = ".".join(str(step) for step in field_path) or part
    return f"{where}: {item['msg']}"


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for validation and HTTP errors; Exception is the rest.
    for raised in (ApiError, Unreadable, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, _handle)
