"""Uvicorn doesn't stop a handler when its browser leaves: a browser could queue and leave."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True, eq=False)
class CancelOnDisconnect:
    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body_in = asyncio.Event()
        answering = False
        left = False

        async def watched_receive() -> Message:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                body_in.set()
            return message

        async def watched_send(message: Message) -> None:
            nonlocal answering
            answering = answering or message["type"] == "http.response.start"
            await send(message)

        async def handle() -> None:
            await self.app(scope, watched_receive, watched_send)

        handler = asyncio.create_task(handle())

        async def cancel_when_left() -> None:
            nonlocal left
            await body_in.wait()
            message = await receive()
            if message["type"] == "http.disconnect" and not answering:
                left = True
                handler.cancel()

        watcher = asyncio.create_task(cancel_when_left())
        try:
            await handler
        except asyncio.CancelledError:
            if not left:
                raise
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
