"""Every failure the API reports, as RFC 9457 Problem Details.

`type` is what the browser branches on and `detail` is shown to the user
verbatim, so it comes from `core/words.py` in plain words. The browser prevents
what it can: by the time the server says no, it's a backstop, never news.
"""

from __future__ import annotations

from enum import Enum

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
        case Unreadable():
            error = ApiError(Problem.DAMAGED)  # from a worker, as the file opened
        case RequestValidationError():
            # FastAPI's body is a list of jargon. Only the browser sends
            # requests, so this is a browser bug: one line for the report.
            lines = []
            for item in exc.errors():
                loc = item["loc"]
                where = ".".join(str(part) for part in loc[1:]) or loc[0]  # drop "body"
                lines.append(f"{where}: {item['msg']}")
            error = ApiError(Problem.INVALID_REQUEST, reason="; ".join(lines))
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


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for validation and HTTP errors; Exception is the rest.
    for raised in (ApiError, Unreadable, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, _handle)
