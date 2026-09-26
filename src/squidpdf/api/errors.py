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
from squidpdf.documents import store


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
    NO_SUCH_PAGE = 422, words.NO_SUCH_PAGE  # not 404, which tells the browser to re-upload
    REDACTION_CONFLICT = 422, words.REDACTION_CONFLICT
    BAD_REFERENCE = 422, words.BAD_REFERENCE
    REDACTION_FAILED = 422, words.REDACTION_FAILED
    FONT_MISMATCH = 422, words.FONT_MISMATCH
    INVALID_REQUEST = 400, words.INVALID_REQUEST
    RATE_LIMITED = 429, words.RATE_LIMITED
    TOO_SLOW = 503, words.TOO_SLOW
    TOO_HEAVY = 422, words.TOO_HEAVY
    TOO_MANY_EDITS = 422, words.TOO_MANY_EDITS
    TEXT_TOO_LONG = 422, words.TEXT_TOO_LONG
    REQUEST_TOO_LARGE = 413, words.REQUEST_TOO_LARGE
    SERVER_ERROR = 500, words.SERVER_ERROR


class ApiError(Exception):
    """Raise anywhere a request is handled and the browser gets Problem Details.

    `fill` fills the sentence's placeholders, e.g. `ApiError(Problem.TOO_LARGE, mb=100)`.
    `debug` is the technical why, for a developer: sent beside `detail`, never in it.
    """

    def __init__(self, problem: Problem, debug: str | None = None, **fill: object) -> None:
        """Word the sentence for this problem."""
        super().__init__(problem.name)
        self.type = problem.name.lower()
        self.status, sentence = problem.value
        self.detail = sentence.format(**fill)
        self.debug = debug


async def _handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets."""
    match exc:
        case ApiError():
            error = exc
        case Unreadable(encrypted=True):
            error = ApiError(Problem.ENCRYPTED)  # from a worker, as the file opened
        case Unreadable():
            error = ApiError(Problem.DAMAGED)
        case store.Gone():
            error = ApiError(Problem.NOT_FOUND)  # expired mid-request: the browser re-uploads
        case RequestValidationError():
            # A browser bug: a plain sentence for the user, FastAPI's list for us.
            debug = "; ".join(_describe(item) for item in exc.errors())
            error = ApiError(Problem.INVALID_REQUEST, debug=debug)
        case HTTPException(status_code=status.HTTP_404_NOT_FOUND):
            error = ApiError(Problem.NOT_FOUND)  # Starlette's own, for an unknown path
        case HTTPException():
            error = ApiError(Problem.INVALID_REQUEST, debug=str(exc.detail))
        case _:
            error = ApiError(Problem.SERVER_ERROR)  # Starlette logs the traceback after
    return problem_response(error)


def problem_response(error: ApiError) -> JSONResponse:
    """An ApiError as Problem Details: the plain `detail`, and `debug` when there is one."""
    body: dict[str, object] = {
        "type": error.type,
        "status": error.status,
        "detail": error.detail,
    }
    if error.debug is not None:
        body["debug"] = error.debug
    return JSONResponse(body, status_code=error.status, media_type="application/problem+json")


def _describe(item: dict[str, Any]) -> str:
    """One validation failure as `field.path: message`."""
    # The part of the request, e.g. "body", then the path to the field inside it.
    part, *field_path = item["loc"]
    where = ".".join(str(step) for step in field_path) or part
    return f"{where}: {item['msg']}"


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for validation and HTTP errors; Exception is the rest.
    handled = (ApiError, Unreadable, store.Gone, RequestValidationError, HTTPException)
    for raised in (*handled, Exception):
        app.add_exception_handler(raised, _handle)
