"""A request whose browser leaves before its answer stops, and so does its PDF work.

Uvicorn doesn't stop a request's handler when the browser goes: a render or an
upload's analysis would run on in a worker for its whole timeout, answering no
one, and a browser could queue many and leave. Cancelling the handler cancels
the pool's future, and pebble stops the worker running it.
"""

from __future__ import annotations

import asyncio
import contextlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "CancelOnDisconnect",
]


class CancelOnDisconnect:
    """Cancels a request's handler when its browser leaves before the answer has begun."""

    def __init__(self, app: ASGIApp) -> None:
        """Watch every request `app` handles."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the handler; once its body is in, listen for the browser leaving."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body_in = asyncio.Event()
        answering = False

        async def watched_receive() -> Message:
            """The browser's messages, noting when the last of its body has come."""
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                body_in.set()
            return message

        async def watched_send(message: Message) -> None:
            """The answer, noting when it has begun: from then on it runs to its end."""
            nonlocal answering
            answering = answering or message["type"] == "http.response.start"
            await send(message)

        async def handle() -> None:
            """The request, as the app would answer it unwatched."""
            await self.app(scope, watched_receive, watched_send)

        handler = asyncio.create_task(handle())

        async def cancel_when_left() -> None:
            """After the body, the next message is the browser leaving, or the answer done."""
            await body_in.wait()
            message = await receive()
            if message["type"] == "http.disconnect" and not answering:
                handler.cancel()

        watcher = asyncio.create_task(cancel_when_left())
        try:
            await handler
        except asyncio.CancelledError:
            # Cancelled because the browser left: nobody to answer. Any other, carry it on.
            if not watcher.done():
                raise
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
