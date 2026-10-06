"""Each address may have so much going at once: jobs at work, and uploads streaming in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from squidpdf.api import constants as limits, pool, rate
from squidpdf.api.app import create_app
from tests.api.conftest import BASE_URL
from tests.helpers import assert_at_least, assert_equal, assert_in, assert_problem, assert_true

_ONE = "192.0.2.1"  # addresses kept for documentation, so nobody's real one
_OTHER = "198.51.100.7"
_PDF = {"content-type": "application/pdf"}
_NOT_A_PDF = b"Dear Sir, please find attached."


@pytest.fixture
def own_app(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """An app of its own, started: every other test sends from the one test address."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    with TestClient(create_app()) as server:  # runs the lifespan: the pool and the sweeper
        yield server


@dataclass
class _Held:
    """A request whose body stops after its first chunk until the test lets it go on."""

    sent: asyncio.Task[httpx.Response]
    reading: asyncio.Event  # set once the server asked for more than its first chunk
    go_on: asyncio.Event


def _held(
    send: Callable[[AsyncIterator[bytes]], Awaitable[httpx.Response]], body: bytes
) -> _Held:
    """Start `send` with `body`, held back after its first byte."""
    reading, go_on = asyncio.Event(), asyncio.Event()

    async def held_back() -> AsyncIterator[bytes]:
        """The body, stopped after its first byte until `go_on`."""
        yield body[:1]
        reading.set()
        await go_on.wait()
        yield body[1:]

    return _Held(asyncio.ensure_future(send(held_back())), reading, go_on)


async def _until(condition: Callable[[], bool]) -> None:
    """Let the server's loop run until `condition` holds."""
    while not condition():
        await asyncio.sleep(0.01)


def _browser(app: FastAPI, address: str) -> httpx.AsyncClient:
    """A browser at `address`, on the app's own loop.

    Not `browser_on`: a TestClient sends each request whole before the next, and these overlap.
    """
    transport = httpx.ASGITransport(app=app, client=(address, 1))
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


@dataclass
class _HeldPool:
    """The app's pool, each job held at its worker until the test lets them all go on."""

    pool: Any  # the app's own, which runs each job once it goes on
    go_on: asyncio.Event = field(default_factory=asyncio.Event)
    at_work: int = 0  # jobs a worker has now

    async def run(self, timeout, task):
        """`task` on the app's pool, once the test says so."""
        self.at_work += 1
        await self.go_on.wait()
        try:
            return await self.pool.run(timeout, task)
        finally:
            self.at_work -= 1

    async def close(self) -> None:
        """The app's pool closes as the app stops."""
        await self.pool.close()


async def _jobs_past_the_turns(app: FastAPI, pdf_bytes: bytes) -> dict[str, Any]:
    """One address's page images at work and waiting, then one more; another's meanwhile."""
    async with _browser(app, _ONE) as one, _browser(app, _OTHER) as other:
        docs = [
            (await browser.post("/api/documents", content=pdf_bytes, headers=_PDF)).json()
            for browser in (one, other)
        ]
        held = _HeldPool(app.state.pool)
        app.state.pool = held
        page_of = [
            f"/api/documents/{doc['id']}/pages/0?scale=1&build={doc['build']}" for doc in docs
        ]
        at_work = asyncio.ensure_future(one.get(page_of[0]))
        await _until(lambda: held.at_work == 1)
        waiting = asyncio.ensure_future(one.get(page_of[0]))
        await _until(lambda: app.state.job_turns.lines[_ONE].in_line == 2)
        one_more = await one.get(page_of[0])
        elsewhere = asyncio.ensure_future(other.get(page_of[1]))
        await _until(lambda: held.at_work == 2)
        held.go_on.set()
        done = [(await sent).status_code for sent in (at_work, waiting, elsewhere)]
        app.state.pool = held.pool
    return {"one_more": one_more, "done": done, "lines_left": dict(app.state.job_turns.lines)}


def test_an_address_past_its_jobs_at_work_waits_its_turn_and_past_those_waiting_is_refused(
    own_app, pdf_bytes, monkeypatch
):
    """One address can't hold every worker, and a browser's burst of page images still loads.

    Another address's job goes to a worker while the first's waits: the wait holds none.
    """
    monkeypatch.setattr(limits, "JOBS_AT_WORK", 1)
    monkeypatch.setattr(limits, "JOBS_WAITING", 1)
    if own_app.portal is None:
        pytest.fail("the app isn't started")
    said = own_app.portal.call(partial(_jobs_past_the_turns, own_app.app, pdf_bytes))

    assert_problem(said["one_more"], "rate_limited", 429)
    assert_equal(
        said["done"], [200, 200, 200], "the one at work, the one that waited, another's"
    )
    assert_equal(said["lines_left"], {}, "lines once nothing is going")


def _depends_on(dependant: Dependant, call: Callable[..., Any]) -> bool:
    """Whether `call` is among what `dependant` depends on, however deep."""
    return any(sub.call is call or _depends_on(sub, call) for sub in dependant.dependencies)


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """Every route the app answers, its own and each included router's."""
    routes = list(app.routes)
    for route in app.routes:
        # An included router is kept whole, with its routes inside it.
        if isinstance(inner := getattr(route, "original_router", None), APIRouter):
            routes += inner.routes
    return [route for route in routes if isinstance(route, APIRoute)]


def test_every_route_that_runs_a_job_takes_a_turn(own_app):
    """A route that reaches the workers can't skip its address's turn."""
    with_workers = [
        route
        for route in _api_routes(own_app.app)
        # The health check sends no job; the font list's is the server's, once for everyone.
        if _depends_on(route.dependant, pool.current)
        and route.path not in ("/api/health", "/api/fonts")
    ]
    assert_at_least(len(with_workers), 7, "routes found that run a job")
    for route in with_workers:
        in_turn = _depends_on(route.dependant, rate.workers_in_turn)
        assert_true(in_turn, f"{route.methods} {route.path} sends its jobs in turn")


async def _uploads_past_the_limit(
    app: FastAPI, pdf_bytes: bytes, font: bytes
) -> dict[str, Any]:
    """A PDF under way, then a font; a font under way, then a PDF; another address meanwhile."""
    async with _browser(app, _ONE) as one, _browser(app, _OTHER) as other:
        doc = (await one.post("/api/documents", content=pdf_bytes, headers=_PDF)).json()
        font_url = f"/api/documents/{doc['id']}/fonts/Any"
        upload = partial(one.post, "/api/documents", headers=_PDF)

        pdf = _held(lambda body: upload(content=body), pdf_bytes)
        await pdf.reading.wait()
        font_meanwhile = await one.put(font_url, content=font)
        elsewhere = await other.post("/api/documents", content=_NOT_A_PDF, headers=_PDF)
        pdf.go_on.set()
        await pdf.sent

        attaching = _held(lambda body: one.put(font_url, content=body), font)
        await attaching.reading.wait()
        pdf_meanwhile = await upload(content=pdf_bytes)
        attaching.go_on.set()
        await attaching.sent

        font_after = await one.put(font_url, content=font)
    return {
        "font_meanwhile": font_meanwhile,
        "pdf_meanwhile": pdf_meanwhile,
        "elsewhere": elsewhere,
        "font_after": font_after,
    }


def test_an_address_past_its_uploads_under_way_is_refused_pdfs_and_fonts_together(
    own_app, pdf_bytes, monkeypatch
):
    """A trickled upload holds disk against the floor: one address can't hold all of it."""
    monkeypatch.setattr(limits, "UPLOADS_UNDER_WAY", 1)
    font = Path(__file__).read_bytes()  # never read as a font: refused or not by its count
    if own_app.portal is None:
        pytest.fail("the app isn't started")
    said = own_app.portal.call(partial(_uploads_past_the_limit, own_app.app, pdf_bytes, font))

    assert_problem(said["font_meanwhile"], "rate_limited", 429)
    assert_problem(said["pdf_meanwhile"], "rate_limited", 429)
    assert_problem(said["elsewhere"], "not_a_pdf", 415)
    assert_in(
        said["font_after"].status_code, range(200, 429), "a font once nothing else is going"
    )
