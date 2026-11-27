from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from starlette.requests import ClientDisconnect
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squidpdf.api import constants as limits
from squidpdf.api.errors import RequestTooLarge
from squidpdf.api.errors.http import response
from squidpdf.api.language import language_of
from squidpdf.core import InvalidRequest

# An exabyte: no body comes near it, and int() refuses a number past 4300 digits.
_MAX_DIGITS = 18


def declared_size(request: Request) -> int | None:
    declared = request.headers.get("content-length")
    if declared is None:
        return None
    digits = declared.strip()
    # Digits only: int() would also take "-5" and "1_000".
    readable = digits.isascii() and digits.isdigit() and len(digits) <= _MAX_DIGITS
    if not readable:
        shown = declared[:_MAX_DIGITS]
        raise InvalidRequest(debug=f"content-length isn't a size in bytes; it starts {shown!r}")
    return int(digits)


@dataclass(frozen=True, slots=True, eq=False)
class BodyLimit:
    app: ASGIApp
    streamed: tuple[BaseRoute, ...]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        passed_on = scope["type"] != "http" or any(
            route.matches(scope)[0] == Match.FULL for route in self.streamed
        )
        if passed_on:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        language = language_of(request)
        try:
            declared = declared_size(request)
        except InvalidRequest as problem:
            await response(problem, language)(scope, receive, send)
            return
        # Read as a module attribute, so a test can lower the limit.
        limit = limits.MAX_BODY_BYTES
        too_large = declared is not None and declared > limit
        try:
            body = None if too_large else await _body_up_to(receive, limit)
        except ClientDisconnect:
            return
        if body is None:
            await response(RequestTooLarge(), language)(scope, receive, send)
            return
        await self.app(scope, _replaying(body, receive), send)


async def _body_up_to(receive: Receive, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        more = message.get("more_body", False)
    return b"".join(chunks)


def _replaying(body: bytes, receive: Receive) -> Receive:
    handed = False

    async def replay() -> Message:
        nonlocal handed
        if handed:
            return await receive()
        handed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay
