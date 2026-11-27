"""Replaces uvicorn's access log, which wrote every document id in the path and query."""

from __future__ import annotations

import time
from dataclasses import dataclass
from http import HTTPMethod

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squidpdf.core import (
    ERROR,
    INFO,
    LogController,
    LogEvent,
    OwnText,
    ms_since,
    start_request,
    tally_fields,
)

_REQUEST_ID_HEADER = "Squid-Request-Id"
_LEFT = 499  # nginx's status for a request whose browser left before the answer
_SERVER_ERROR = 500

_log = LogController.for_module(__name__)


@dataclass(frozen=True, slots=True, eq=False)
class AccessLog:
    app: ASGIApp
    unlogged: frozenset[str]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = start_request()
        started = time.monotonic()
        status = _LEFT

        async def named_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[_REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, named_send)
        finally:
            if scope["path"] not in self.unlogged:
                _log_request(scope, status=status, started=started)


def _log_request(scope: Scope, *, status: int, started: float) -> None:
    route = scope.get("route")
    template = {} if route is None else {"route": OwnText(route.path)}
    method = HTTPMethod.__members__.get(scope["method"])
    named_method = {} if method is None else {"method": method}
    level = ERROR if status >= _SERVER_ERROR else INFO
    _log.write(
        LogEvent.REQUEST_DONE,
        level=level,
        **named_method,
        **template,
        status=status,
        ms=ms_since(started),
        **tally_fields(),
    )
