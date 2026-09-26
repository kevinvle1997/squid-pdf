"""A Problem as the Problem Details the browser gets, whatever was raised."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status
from starlette.exceptions import HTTPException

from squidpdf.api.errors.generic import InvalidRequest, NotFound, ServerError
from squidpdf.api.language import headers, of
from squidpdf.core import Problem


def _from_validation(exc: Exception) -> Problem:
    """FastAPI's list of what was wrong, kept for a developer."""
    assert isinstance(exc, RequestValidationError)
    return InvalidRequest(debug="; ".join(_describe(item) for item in exc.errors()))


def _from_http(exc: Exception) -> Problem:
    """Starlette's own: a 404 for an unknown path, anything else a bad request."""
    assert isinstance(exc, HTTPException)
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return NotFound()
    return InvalidRequest(debug=str(exc.detail))


# Exceptions from outside our code, and the Problem each one means.
_ADOPT: list[tuple[type[Exception], Callable[[Exception], Problem]]] = [
    (RequestValidationError, _from_validation),
    (HTTPException, _from_http),
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
    body: dict[str, object] = {
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
        headers=headers(language),
    )


async def _handle(request: Request, exc: Exception) -> Response:
    """Whatever was raised, answered as the Problem Details the browser gets."""
    return response(adopt(exc), of(request))


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
