"""A Problem as the Problem Details the browser gets, whatever was raised."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated, Any, NotRequired, TypedDict, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic.json_schema import JsonDict
from starlette import status
from starlette.exceptions import HTTPException

from squidpdf.api.constants import WORKER_FAILURES
from squidpdf.api.errors.generic import MethodNotAllowed, ServerError
from squidpdf.api.language import language_of
from squidpdf.core import (
    CORE_ERRORS,
    Failure,
    InvalidRequest,
    NotFound,
    Param,
    Problem,
    words,
)

_logger = logging.getLogger(__name__)


def _each_subclass_of(problem: type[Problem]) -> Iterator[type[Problem]]:
    """`problem` and every subclass under it, however deep."""
    yield problem
    for child in problem.__subclasses__():
        yield from _each_subclass_of(child)


def _list_every_type(schema: JsonDict) -> None:
    """ProblemInfo's `type` in the OpenAPI: one of every Problem's, so the browser knows each.

    Worked out when the OpenAPI is first asked for, once the app has imported
    every feature, and so every Problem.
    """
    # Unpacked into a new list, which mypy reads as JSON: a list[str] is not one.
    schema["enum"] = [*sorted({cls.type for cls in _each_subclass_of(Problem)})]


class ProblemInfo(TypedDict):
    """A Problem as the browser gets it: RFC 9457 Problem Details, plus its Message unsaid.

    `type` is one of every Problem's, which the OpenAPI lists. `detail` is shown
    verbatim; `code` is always `type`, and `params` fill it.
    """

    type: Annotated[str, Field(json_schema_extra=_list_every_type)]
    status: int
    detail: str
    code: str
    params: dict[str, Param]
    debug: NotRequired[
        Annotated[
            str,
            Field(
                description="For a developer: what exactly was wrong with the request. Sent on "
                "a 4xx only: a 5xx's can name a file on the server, so only its log has it."
            ),
        ]
    ]


def _from_validation(exc: Exception) -> Problem:
    """FastAPI's list of what was wrong, kept for a developer."""
    invalid = cast(RequestValidationError, exc)  # _FRAMEWORK_FAILURES hands it only these
    return InvalidRequest(debug="; ".join(_describe(item) for item in invalid.errors()))


# Starlette's own failures a Problem keeps the status of; any other is a bad request.
_HTTP_PROBLEMS: dict[int, type[Problem]] = {
    status.HTTP_404_NOT_FOUND: NotFound,  # an unknown path
    status.HTTP_405_METHOD_NOT_ALLOWED: MethodNotAllowed,  # a path asked the wrong way
}


def _from_http(exc: Exception) -> Problem:
    """Starlette's own: a missing path or a wrong method as itself, else a bad request."""
    failure = cast(HTTPException, exc)  # _FRAMEWORK_FAILURES hands it only these
    # .get: most of Starlette's failures are the browser's, said as a bad request.
    problem_type = _HTTP_PROBLEMS.get(failure.status_code)
    if problem_type is None:
        return InvalidRequest(debug=str(failure.detail))
    return problem_type()


# The framework's failures, each the Problem it means. Neither type is the other's.
_FRAMEWORK_FAILURES = (
    Failure(raised=RequestValidationError, problem=_from_validation),
    Failure(raised=HTTPException, problem=_from_http),
)
# The API's own: core's rows (MuPDF's), then pebble's, then the framework's.
API_ERRORS = CORE_ERRORS.with_rows(*WORKER_FAILURES, *_FRAMEWORK_FAILURES)


def _adopt(exc: Exception) -> Problem:
    """Any exception as the Problem `API_ERRORS` says it means; a bug if nothing claims it."""
    if isinstance(exc, Problem):
        return exc
    claimed = API_ERRORS.problem_if_claimed(exc)
    # A bug: said without its text, which is for the log. Starlette logs the traceback after.
    if claimed is None:
        return ServerError()
    return claimed


def _debug_sent(problem: Problem) -> str | None:
    """The `debug` the browser gets: a 4xx's; a 5xx's goes to the log instead."""
    # No why to send.
    if problem.debug is None:
        return None
    # A failure on our side: its why is for whoever reads the log, not whoever caused it.
    if problem.status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        _logger.warning("Sent %s without its debug: %s", problem.type, problem.debug)
        return None
    # The request's fault: its why tells the browser's developer what to fix.
    return problem.debug


def response(problem: Problem, language: str) -> JSONResponse:
    """A Problem as Problem Details, `detail` in `language`; its `debug` sent on a 4xx only.

    `code` and `params` are its Message, so the browser can say it in its own words;
    `code` is always `type`. A 5xx's `debug` is the server's own text, such as MuPDF's
    naming a file on the server, so it goes to the log with its `type` instead.
    """
    body: ProblemInfo = {
        "type": problem.type,
        "status": problem.status,
        **words.said(problem.message, language),
    }
    debug = _debug_sent(problem)
    # Left out, not null, when there is none to send.
    if debug is not None:
        body["debug"] = debug
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type="application/problem+json",
        headers=words.language_headers(language),
    )


async def _handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets.

    Starlette's own headers ride along: a 405's Allow says the methods the path takes.
    """
    answer = response(_adopt(exc), language_of(request))
    if isinstance(exc, HTTPException) and exc.headers:
        answer.headers.update(exc.headers)
    return answer


def _describe(item: dict[str, Any]) -> str:
    """One validation failure as `field.path: message`."""
    # The part of the request, e.g. "body", then the path to the field inside it.
    part, *field_path = item["loc"]
    where = ".".join(str(step) for step in field_path) or part
    return f"{where}: {item['msg']}"


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for validation and HTTP errors; Exception is the rest.
    for raised in (Problem, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, _handle)
