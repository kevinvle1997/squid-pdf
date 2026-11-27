from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, TypedDict, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic.json_schema import JsonDict
from starlette import status
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squidpdf.api.constants import WORKER_FAILURES
from squidpdf.api.errors.generic import MethodNotAllowed, ServerError
from squidpdf.api.language import language_of
from squidpdf.core import (
    CORE_ERRORS,
    Failure,
    InvalidRequest,
    LogController,
    LogEvent,
    NotFound,
    Param,
    Problem,
    words,
)

_log = LogController.for_module(__name__)


def _each_subclass_of(problem: type[Problem]) -> Iterator[type[Problem]]:
    yield problem
    for child in problem.__subclasses__():
        yield from _each_subclass_of(child)


def _list_every_type(schema: JsonDict) -> None:
    # Unpacked into a new list, which mypy reads as JSON: a list[str] is not one.
    schema["enum"] = [*sorted({cls.type for cls in _each_subclass_of(Problem)})]


class ProblemInfo(TypedDict):
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
    invalid = cast(RequestValidationError, exc)
    return InvalidRequest(debug="; ".join(_describe(item) for item in invalid.errors()))


_HTTP_PROBLEMS: dict[int, type[Problem]] = {
    status.HTTP_404_NOT_FOUND: NotFound,
    status.HTTP_405_METHOD_NOT_ALLOWED: MethodNotAllowed,
}


def _from_http(exc: Exception) -> Problem:
    failure = cast(HTTPException, exc)
    problem_type = _HTTP_PROBLEMS.get(failure.status_code)
    if problem_type is None:
        return InvalidRequest(debug=str(failure.detail))
    return problem_type()


_FRAMEWORK_FAILURES = (
    Failure(raised=RequestValidationError, problem=_from_validation),
    Failure(raised=HTTPException, problem=_from_http),
)
API_ERRORS = CORE_ERRORS.with_rows(*WORKER_FAILURES, *_FRAMEWORK_FAILURES)


def _adopt(exc: Exception) -> Problem:
    if isinstance(exc, Problem):
        return exc
    claimed = API_ERRORS.problem_if_claimed(exc)
    if claimed is None:
        _log.write(LogEvent.UNHANDLED, exc)
        return ServerError()
    return claimed


def _debug_sent(problem: Problem) -> str | None:
    if problem.debug is None:
        return None
    # A 5xx's debug can name a file on the server, so only the log gets it.
    if problem.status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        _log.write(LogEvent.PROBLEM_SENT_WITHOUT_DEBUG, problem)
        return None
    return problem.debug


def response(problem: Problem, language: str) -> JSONResponse:
    body: ProblemInfo = {
        "type": problem.type,
        "status": problem.status,
        **words.said(problem.message, language),
    }
    debug = _debug_sent(problem)
    if debug is not None:
        body["debug"] = debug
    return JSONResponse(
        body,
        status_code=problem.status,
        media_type="application/problem+json",
        headers=words.language_headers(language),
    )


async def _handle(request: Request, exc: Exception) -> Response:
    answer = response(_adopt(exc), language_of(request))
    if isinstance(exc, HTTPException) and exc.headers:
        answer.headers.update(exc.headers)
    return answer


@dataclass(frozen=True, slots=True, eq=False)
class BugBoundary:
    """Starlette's catch-all answers outside every middleware, and the server logs it again."""

    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        answering = False

        async def watched_send(message: Message) -> None:
            nonlocal answering
            answering = answering or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, watched_send)
        except Exception as bug:
            # Half an answer is out: nothing to answer with, so it goes on up.
            if answering:
                raise
            answer = await _handle(Request(scope), bug)
            await answer(scope, receive, send)


def _describe(item: dict[str, Any]) -> str:
    part, *field_path = item["loc"]
    where = ".".join(str(step) for step in field_path) or part
    return f"{where}: {item['msg']}"


def install(app: FastAPI) -> None:
    for raised in (Problem, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(raised, _handle)
