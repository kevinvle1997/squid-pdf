"""Every failure the API reports, as RFC 9457 Problem Details.

`type` is what the browser branches on and `detail` is shown to the user
verbatim, so it comes from `core/words.py` in plain words. The browser prevents
what it can: by the time the server says no, it's a backstop, never news.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
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


class ApiError(Exception):
    """Raise anywhere a request is handled and the browser gets Problem Details.

    `fill` fills the sentence's placeholders, e.g. `ApiError("too_large", mb=100)`.
    """

    def __init__(self, type: ProblemType, **fill: object) -> None:
        """Look up the status and word the sentence for this `type`."""
        super().__init__(type)
        self.type = type
        self.status, sentence = _PROBLEMS[type]
        self.detail = sentence.format(**fill)


async def _handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets."""
    match exc:
        case ApiError():
            problem = exc
        case RequestValidationError():
            # FastAPI's body is a list of jargon. Only the browser sends
            # requests, so this is a browser bug: one line for the report.
            lines = []
            for error in exc.errors():
                loc = error["loc"]
                where = ".".join(str(part) for part in loc[1:]) or loc[0]  # drop "body"
                lines.append(f"{where}: {error['msg']}")
            problem = ApiError("invalid_request", problem="; ".join(lines))
        case HTTPException(status_code=status.HTTP_404_NOT_FOUND):
            problem = ApiError("not_found")  # Starlette's own, for an unknown path
        case HTTPException():
            problem = ApiError("invalid_request", problem=exc.detail)
        case _:
            problem = ApiError("server_error")  # Starlette logs the traceback after
    return JSONResponse(
        {"type": problem.type, "status": problem.status, "detail": problem.detail},
        status_code=problem.status,
        media_type="application/problem+json",
    )


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for the first three; Exception catches the rest.
    for raised in (ApiError, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, _handle)
