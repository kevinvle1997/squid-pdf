"""One line per request, by its route's template, and an id the browser can quote.

It replaces uvicorn's access log (`--no-access-log`), which wrote the full path
and query, and so every document id. Outermost, so a request its browser left
is still logged, as 499.
"""

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

# The reply's header naming the request, so a bug report points at its lines.
REQUEST_ID_HEADER = "Squid-Request-Id"
_LEFT = 499  # nginx's status for a request whose browser left before the answer
_SERVER_ERROR = 500  # from here up, the server's failure: logged as an ERROR

_log = LogController.for_module(__name__)


@dataclass(frozen=True, slots=True, eq=False)
class AccessLog:
    """Logs each request when it ends, and names it in its reply."""

    app: ASGIApp  # every request it handles is logged, but those to `unlogged`
    unlogged: frozenset[str]  # paths no line is written for: the health check

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the request, its lines carrying a new id; then log how it ended."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = start_request()
        started = time.monotonic()
        status = _LEFT  # until an answer begins: none does when the browser leaves first

        async def named_send(message: Message) -> None:
            """The answer, its start carrying the request's id and noting its status."""
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, named_send)
        finally:
            if scope["path"] not in self.unlogged:
                _log_request(scope, status=status, started=started)


def _log_request(scope: Scope, *, status: int, started: float) -> None:
    """The request's one line: method, route template, status, time taken, what it tallied."""
    route = scope.get("route")  # None when no route matched: a 404
    template = {} if route is None else {"route": OwnText(route.path)}
    # .get: a method no route takes still comes in, and gets a 405.
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
