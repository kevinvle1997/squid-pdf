"""Each address may have so much going at once: jobs at work, and uploads streaming in."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from squidpdf.api import pool, rate
from tests.api.conftest import BASE_URL, NOT_A_PDF, ONE_ADDRESS, OTHER_ADDRESS
from tests.helpers import assert_at_least, assert_equal, assert_in, assert_problem, assert_true

_PDF = {"content-type": "application/pdf"}
_SETTLE_S = 0.2  # far past the loop's time to hand a waiting job a turn that's free
_RENDER_STARTS_S = 5  # far past a render's wait for a free turn; behind page images, forever


@dataclass(slots=True)
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


@dataclass(slots=True)
class _HeldPool:
    """The app's pool, each job held at its worker until the test lets them all go on."""

    pool: Any  # the app's own, which runs each job once it goes on
    go_on: asyncio.Event = field(default_factory=asyncio.Event)
    at_work: list[str] = field(default_factory=list)  # what each job a worker has now runs

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task` on the app's pool, once the test says so."""
        job = task.func if isinstance(task, partial) else task  # each is a partial of its job
        name = getattr(job, "__name__", "")  # .getattr: a callable needn't have a name
        self.at_work.append(name)
        await self.go_on.wait()
        try:
            return await self.pool.run(timeout, task)
        finally:
            self.at_work.remove(name)

    async def close(self) -> None:
        """The app's pool closes as the app stops."""
        await self.pool.close()


async def _jobs_past_the_turns(app: FastAPI, pdf_bytes: bytes) -> dict[str, Any]:
    """One address's page images at work and waiting, then one more; another's meanwhile."""
    async with _browser(app, ONE_ADDRESS) as one, _browser(app, OTHER_ADDRESS) as other:
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
        await _until(lambda: len(held.at_work) == 1)
        waiting = asyncio.ensure_future(one.get(page_of[0]))
        await _until(lambda: app.state.job_turns.lines[ONE_ADDRESS].in_line == 2)
        one_more = await one.get(page_of[0])
        elsewhere = asyncio.ensure_future(other.get(page_of[1]))
        await _until(lambda: len(held.at_work) == 2)
        held.go_on.set()
        done = [(await sent).status_code for sent in (at_work, waiting, elsewhere)]
        app.state.pool = held.pool
    return {"one_more": one_more, "done": done, "lines_left": dict(app.state.job_turns.lines)}


def test_an_address_past_its_jobs_at_work_waits_its_turn_and_past_those_waiting_is_refused(
    own_app, pdf_bytes
):
    """One address can't hold every worker, and a browser's burst of page images still loads.

    Another address's job goes to a worker while the first's waits: the wait holds none.
    """
    with own_app.started(JOBS_AT_WORK=1, JOBS_WAITING=1) as server:
        if server.portal is None:
            pytest.fail("the app isn't started")
        said = server.portal.call(partial(_jobs_past_the_turns, own_app.app, pdf_bytes))

    assert_problem(said["one_more"], "rate_limited", 429)
    assert_equal(
        said["done"], [200, 200, 200], "the one at work, the one that waited, another's"
    )
    assert_equal(said["lines_left"], {}, "lines once nothing is going")


async def _render_behind_page_images(app: FastAPI, pdf_bytes: bytes) -> dict[str, Any]:
    """More page images than the address has turns, then a render: what's at work then."""
    async with _browser(app, ONE_ADDRESS) as one:
        doc = (await one.post("/api/documents", content=pdf_bytes, headers=_PDF)).json()
        held = _HeldPool(app.state.pool)
        app.state.pool = held
        page = f"/api/documents/{doc['id']}/pages/0?scale=1&build={doc['build']}"
        images = [asyncio.ensure_future(one.get(page)) for _ in range(4)]
        await _until(lambda: len(held.at_work) >= 2)
        await asyncio.sleep(_SETTLE_S)  # long enough for a third image to take a turn it had
        body = {"edits": [], "regions": [{"page": 0}], "scale": 1}
        render = asyncio.ensure_future(
            one.post(f"/api/documents/{doc['id']}/render", json=body)
        )
        try:
            async with asyncio.timeout(_RENDER_STARTS_S):
                await _until(lambda: "_draw_regions" in held.at_work)
            started = True
        except TimeoutError:  # raised while the render waits behind the page images
            started = False
        at_work = sorted(held.at_work)
        held.go_on.set()
        done = [(await sent).status_code for sent in (*images, render)]
        app.state.pool = held.pool
    return {"started": started, "at_work": at_work, "done": done}


def test_a_render_sent_while_page_images_fill_the_turns_starts_without_waiting_for_them(
    own_app, pdf_bytes
):
    """A browser asks for the pages near the view at once: its edits still settle in their time.

    Page images take all but one of the address's turns, so the last is the render's.
    """
    with own_app.started(JOBS_AT_WORK=3, JOBS_WAITING=30) as server:
        if server.portal is None:
            pytest.fail("the app isn't started")
        said = server.portal.call(partial(_render_behind_page_images, own_app.app, pdf_bytes))

    assert_true(said["started"], f"the render reached a worker; at work: {said['at_work']}")
    assert_equal(
        said["at_work"], ["_draw_page", "_draw_page", "_draw_regions"], "jobs at work then"
    )
    assert_equal(said["done"], [200] * 5, "the page images and the render, once let go")


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
    async with _browser(app, ONE_ADDRESS) as one, _browser(app, OTHER_ADDRESS) as other:
        doc = (await one.post("/api/documents", content=pdf_bytes, headers=_PDF)).json()
        font_url = f"/api/documents/{doc['id']}/fonts/Any"
        upload = partial(one.post, "/api/documents", headers=_PDF)

        pdf = _held(lambda body: upload(content=body), pdf_bytes)
        await pdf.reading.wait()
        font_meanwhile = await one.put(font_url, content=font)
        elsewhere = await other.post("/api/documents", content=NOT_A_PDF, headers=_PDF)
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
    own_app, pdf_bytes
):
    """A trickled upload holds disk against the floor: one address can't hold all of it."""
    font = Path(__file__).read_bytes()  # never read as a font: refused or not by its count
    with own_app.started(UPLOADS_UNDER_WAY=1) as server:
        if server.portal is None:
            pytest.fail("the app isn't started")
        said = server.portal.call(
            partial(_uploads_past_the_limit, own_app.app, pdf_bytes, font)
        )

    assert_problem(said["font_meanwhile"], "rate_limited", 429)
    assert_problem(said["pdf_meanwhile"], "rate_limited", 429)
    assert_problem(said["elsewhere"], "not_a_pdf", 415)
    assert_in(
        said["font_after"].status_code, range(200, 429), "a font once nothing else is going"
    )
