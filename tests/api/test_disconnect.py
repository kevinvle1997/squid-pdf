"""At the ASGI level, not through TestClient: TestClient's browser can't leave."""

from __future__ import annotations

import asyncio
import contextlib
import time

from starlette.types import Message, Receive, Scope, Send

from squidpdf.api.app import create_app
from squidpdf.api.disconnect import CancelOnDisconnect
from tests.helpers import assert_at_most, assert_equal, assert_true

_WORK_S = 30  # far longer than the test waits: work that finished wasn't stopped
_LEAVES_AFTER_S = 0.05
_ENOUGH_S = 5
_HTTP: Scope = {"type": "http", "method": "POST", "path": "/api/documents/x/render"}


def _browser_that_leaves() -> Receive:
    sent_body = False

    async def receive() -> Message:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(_LEAVES_AFTER_S)
        return {"type": "http.disconnect"}

    return receive


async def _nowhere(_message: Message) -> None: ...


def test_work_for_a_browser_that_left_is_stopped():
    stopped = False

    async def slow(_scope: Scope, receive: Receive, _send: Send) -> None:
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
    sent: list[str] = []

    async def answers_then_finishes(_scope: Scope, receive: Receive, send: Send) -> None:
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


def test_a_request_the_server_cancels_after_its_browser_left_ends_cancelled():
    async def answers_slowly(_scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(_WORK_S)

    async def cancelled_by_the_server() -> bool:
        watched = CancelOnDisconnect(answers_slowly)
        request = asyncio.ensure_future(watched(_HTTP, _browser_that_leaves(), _nowhere))
        await asyncio.sleep(_LEAVES_AFTER_S * 4)
        request.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request
        return request.cancelled()

    assert_true(asyncio.run(cancelled_by_the_server()), "the request ended cancelled")


def test_a_get_whose_browser_left_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    app = create_app()
    stopped = False

    async def slow_get() -> None:
        nonlocal stopped
        try:
            await asyncio.sleep(_WORK_S)
        except asyncio.CancelledError:
            stopped = True
            raise

    app.add_api_route("/api/slow", slow_get)
    get: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/slow",
        "headers": [],
        "query_string": b"",
    }
    started = time.monotonic()
    asyncio.run(app(get, _browser_that_leaves(), _nowhere))

    assert_true(stopped, "the GET for a browser that left was stopped")
    took = time.monotonic() - started
    assert_at_most(took, _ENOUGH_S, "seconds it took to stop")
