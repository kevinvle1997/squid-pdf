"""A request whose browser leaves before its answer stops; one being answered runs on.

At the ASGI level, not through TestClient: TestClient's browser can't leave.
The messages here are what uvicorn sends: the body, then `http.disconnect`
when the browser goes (or when the answer is done).
"""

from __future__ import annotations

import asyncio
import time

from starlette.types import Message, Receive, Scope, Send

from squidpdf.api.disconnect import CancelOnDisconnect
from tests.helpers import assert_at_most, assert_equal, assert_true

_WORK_S = 30  # far longer than the test waits: work that finished wasn't stopped
_LEAVES_AFTER_S = 0.05
_ENOUGH_S = 5
_HTTP: Scope = {"type": "http", "method": "POST", "path": "/api/documents/x/render"}


def _browser_that_leaves() -> Receive:
    """Sends an empty body, then leaves a moment later."""
    sent_body = False

    async def receive() -> Message:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(_LEAVES_AFTER_S)
        return {"type": "http.disconnect"}

    return receive


async def _nowhere(_message: Message) -> None:
    """A browser that's gone: what's sent goes nowhere."""


def test_work_for_a_browser_that_left_is_stopped():
    stopped = False

    async def slow(_scope: Scope, receive: Receive, _send: Send) -> None:
        """Reads the body, then works far longer than the browser waits."""
        nonlocal stopped
        await receive()
        try:
            await asyncio.sleep(_WORK_S)
        except asyncio.CancelledError:
            stopped = True
            raise

    started = time.monotonic()
    asyncio.run(CancelOnDisconnect(slow)(_HTTP, _browser_that_leaves(), _nowhere))

    assert_true(stopped, "the work for a browser that left was stopped")
    took = time.monotonic() - started
    assert_at_most(took, _ENOUGH_S, "seconds it took to stop")


def test_an_answer_already_begun_runs_to_its_end():
    """Once the answer has started, the browser leaving is its end, not a reason to stop."""
    sent: list[str] = []

    async def answers_then_finishes(_scope: Scope, receive: Receive, send: Send) -> None:
        """Starts its answer at once, then takes a moment over the rest."""
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(_LEAVES_AFTER_S * 4)
        await send({"type": "http.response.body", "body": b"done"})

    async def record(message: Message) -> None:
        sent.append(message["type"])

    asyncio.run(
        CancelOnDisconnect(answers_then_finishes)(_HTTP, _browser_that_leaves(), record)
    )

    assert_equal(sent, ["http.response.start", "http.response.body"], "what was sent")
