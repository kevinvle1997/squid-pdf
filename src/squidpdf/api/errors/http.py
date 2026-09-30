"""A Problem as the Problem Details the browser gets, whatever was raised."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NotRequired, TypedDict

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException

from squidpdf.api.errors.generic import (
    InvalidRequest,
    MethodNotAllowed,
    NotFound,
    ServerError,
)
from squidpdf.api.language import language_of
from squidpdf.core import Param, Problem, words

__all__ = [
    "ProblemInfo",
    "PROBLEM_RESPONSES",
    "adopt",
    "response",
    "install",
]


class ProblemInfo(TypedDict):
    """A Problem as the browser gets it: RFC 9457 Problem Details, plus its Message unsaid.

    `detail` is shown verbatim; `code` is always `type`, and `params` fill it.
    """

    type: str
    status: int
    detail: str
    code: str
    params: dict[str, Param]
    debug: NotRequired[str]  # for a developer: what exactly was wrong with the request


# Every route can answer with a Problem, so the OpenAPI says so and the browser's types have it.
# `model` puts ProblemInfo in the schemas; the content names the type it's really sent as.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {
        "model": ProblemInfo,
        "description": "Problem Details: what went wrong, in the reader's words",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemInfo"}}
        },
    },
}


def from_validation(exc: Exception) -> Problem:
    """FastAPI's list of what was wrong, kept for a developer."""
    assert isinstance(exc, RequestValidationError)
    return InvalidRequest(debug="; ".join(describe(item) for item in exc.errors()))


# Starlette's own failures a Problem keeps the status of; any other is a bad request.
_HTTP_PROBLEMS: dict[int, type[Problem]] = {
    status.HTTP_404_NOT_FOUND: NotFound,  # an unknown path
    status.HTTP_405_METHOD_NOT_ALLOWED: MethodNotAllowed,  # a path asked the wrong way
}


def from_http(exc: Exception) -> Problem:
    """Starlette's own: a missing path or a wrong method as itself, else a bad request."""
    assert isinstance(exc, HTTPException)
    # .get: most of Starlette's failures are the browser's, said as a bad request.
    problem = _HTTP_PROBLEMS.get(exc.status_code)
    if problem is None:
        return InvalidRequest(debug=str(exc.detail))
    return problem()


# Exceptions from outside our code, and the Problem each one means.
_ADOPT: list[tuple[type[Exception], Callable[[Exception], Problem]]] = [
    (RequestValidationError, from_validation),
    (HTTPException, from_http),
]


def adopt(exc: Exception) -> Problem:
    """Any exception as the Problem it means; a bug if nothing claims it."""
    if isinstance(exc, Problem):
        return exc
    for raised, make in _ADOPT:
        if isinstance(exc, raised):
            return make(exc)
    return ServerError()  # Starlette logs the traceback after


def response(problem: Problem, language: str) -> JSONResponse:
    """A Problem as Problem Details, `detail` in `language`, and `debug` when there is one.

    `code` and `params` are its Message, so the browser can say it in its own words;
    `code` is always `type`.
    """
    body: ProblemInfo = {
        "type": problem.type,
        "status": problem.status,
        "detail": problem.said_in(language),
        "code": problem.type,
        "params": problem.fill,
    }
    if problem.debug is not None:
        body["debug"] = problem.debug
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type="application/problem+json",
        headers=words.language_headers(language),
    )


async def handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets.

    Starlette's own headers ride along: a 405's Allow says the methods the path takes.
    """
    answer = response(adopt(exc), language_of(request))
    if isinstance(exc, HTTPException) and exc.headers:
        answer.headers.update(exc.headers)
    return answer


def describe(item: dict[str, Any]) -> str:
    """One validation failure as `field.path: message`."""
    # The part of the request, e.g. "body", then the path to the field inside it.
    part, *field_path = item["loc"]
    where = ".".join(str(step) for step in field_path) or part
    return f"{where}: {item['msg']}"


def install(app: FastAPI) -> None:
    """Make every error the app can raise leave as Problem Details."""
    # FastAPI has its own handlers for validation and HTTP errors; Exception is the rest.
    for raised in (Problem, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, handle)
