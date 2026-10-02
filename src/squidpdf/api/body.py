"""A request's body: the size it states, and how much of one the server will read."""

from __future__ import annotations

from collections.abc import Collection

from fastapi import Request
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from squidpdf.api import constants as limits
from squidpdf.api.errors import RequestTooLarge
from squidpdf.api.errors.http import response
from squidpdf.api.language import language_of
from squidpdf.core import InvalidRequest

__all__ = [
    "declared_size",
    "BodyLimit",
]

# An exabyte: no body comes near it, and int() refuses a number past 4300 digits.
_MAX_DIGITS = 18


def declared_size(request: Request) -> int | None:
    """The body's size as the request states it, or None; InvalidRequest if not a number."""
    declared = request.headers.get("content-length")  # absent when the body is chunked
    if declared is None:
        return None
    digits = declared.strip()
    # Digits only: int() would also take "-5" and "1_000".
    readable = digits.isascii() and digits.isdigit() and len(digits) <= _MAX_DIGITS
    if not readable:
        shown = declared[:_MAX_DIGITS]
        raise InvalidRequest(debug=f"content-length isn't a size in bytes; it starts {shown!r}")
    return int(digits)


class BodyLimit:
    """Reads a request's body before its route does, and refuses one past MAX_BODY_BYTES.

    However it's sent: a size stated up front is checked before any is read,
    and a body sent in chunks, which states none, is counted as it comes. A
    route read into memory whole would otherwise take any size. `streamed`
    are the paths that read their own body as it streams, with their own
    limit (uploads); theirs passes through unread.
    """

    def __init__(self, app: ASGIApp, *, streamed: Collection[str]) -> None:
        """Guard `app`, all but the POSTs to `streamed`."""
        self.app = app
        self.streamed = frozenset(streamed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer too large or a bad size; otherwise hand the app the body, read whole."""
        is_post = scope["type"] == "http" and scope["method"] == "POST"
        if scope["type"] != "http" or (is_post and scope["path"] in self.streamed):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        language = language_of(request)
        try:
            declared = declared_size(request)
        except InvalidRequest as problem:  # the size it states isn't a whole number
            await response(problem, language)(scope, receive, send)
            return
        # Read as a module attribute, so a test can lower the limit.
        limit = limits.MAX_BODY_BYTES
        too_large = declared is not None and declared > limit
        try:
            body = None if too_large else await body_up_to(receive, limit)
        except ClientDisconnect:  # the browser left mid-body: nobody to answer
            return
        if body is None:
            await response(RequestTooLarge(), language)(scope, receive, send)
            return
        await self.app(scope, replaying(body, receive), send)


async def body_up_to(receive: Receive, limit: int) -> bytes | None:
    """The whole body, or None as soon as it passes `limit` bytes."""
    chunks: list[bytes] = []
    size = 0
    more = True
    while more:
        message = await receive()
        # The browser left before sending it all.
        if message["type"] == "http.disconnect":
            raise ClientDisconnect
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        more = message.get("more_body", False)
    return b"".join(chunks)


def replaying(body: bytes, receive: Receive) -> Receive:
    """A receive that hands the app `body` whole, then whatever comes after it."""
    handed = False

    async def replay() -> Message:
        """The body first, as one message; after that, the browser's own messages."""
        nonlocal handed
        if handed:
            return await receive()
        handed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay
