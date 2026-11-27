from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.api import app as api_app
from squidpdf.api.errors import TooSlow
from squidpdf.core import BUILD
from squidpdf.editing import font_list
from squidpdf.editing.font_list import _FontListTask  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.editing.types import FontList
from tests.api.conftest import BASE_URL
from tests.conftest import log_lines
from tests.helpers import assert_equal, assert_every_field_filled, assert_problem

_BROWSERS = 2
# Long enough that every browser asks while the first measurement still runs.
_MEASURING_S = 0.2


@dataclass(slots=True)
class _StandInWorkers:
    too_slow: int = 0
    runs: int = 0

    async def run(self, timeout: float, task: Callable[[], FontList]) -> FontList:
        self.runs += 1
        if self.runs <= self.too_slow:
            raise TooSlow()
        await asyncio.sleep(_MEASURING_S)
        return {"build": BUILD, "families": []}

    async def close(self) -> None: ...


@pytest.fixture
def unlisted() -> Iterator[None]:
    # The server keeps its list for the process, so the shared app's start measured it.
    font_list._font_list_task.forget()
    yield
    font_list._font_list_task.forget()


async def _ask_at_once(app: FastAPI) -> list[httpx.Response]:
    """In the app's own loop: a TestClient sends each request whole before the next."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as browser:
        asks = (browser.get("/api/fonts", params={"build": BUILD}) for _ in range(_BROWSERS))
        return await asyncio.gather(*asks)


def test_browsers_asking_for_the_font_list_at_once_wait_for_one_measurement(
    app, server: TestClient, monkeypatch, unlisted
):
    workers = _StandInWorkers()
    monkeypatch.setattr(app.state, "pool", workers)

    if server.portal is None:
        pytest.fail("the app isn't started")
    replies = server.portal.call(_ask_at_once, app)

    assert_equal([reply.status_code for reply in replies], [200] * _BROWSERS, "statuses")
    assert_equal(workers.runs, 1, "measurements")


def test_a_font_list_that_ran_out_of_time_is_measured_again(
    app, browser, monkeypatch, unlisted
):
    workers = _StandInWorkers(too_slow=1)
    monkeypatch.setattr(app.state, "pool", workers)

    first = browser().get("/api/fonts", params={"build": BUILD})
    assert_problem(first, "too_slow", 503)
    again = browser().get("/api/fonts", params={"build": BUILD})
    assert_equal(again.status_code, 200, "status the second time")
    assert_equal(workers.runs, 2, "measurements")


@pytest.mark.parametrize(
    ("too_slow", "logged", "measurements"), [(0, False, 1), (1, True, 2)], ids=["ran", "failed"]
)
def test_a_server_measures_its_font_list_as_it_starts(
    own_app, monkeypatch, unlisted, capsys, too_slow, logged, measurements
):
    workers = _StandInWorkers(too_slow=too_slow)
    monkeypatch.setattr(api_app, "start_pool", lambda: workers)
    with own_app.started() as server:
        if server.portal is None:
            pytest.fail("the client hasn't started the app")
        server.portal.call(asyncio.sleep, 0)
        measured_before_any_ask = workers.runs
        reply = server.get("/api/fonts", params={"build": BUILD})

    said = [" ".join(line.split()[1:5]) for line in log_lines(capsys.readouterr().out)]
    assert_equal(measured_before_any_ask, 1, "measurements before anyone asked")
    failure_logged = "ERROR failed editing.font_list font_list_failed" in said
    assert_equal(failure_logged, logged, "the failure, logged")
    assert_equal(
        (reply.status_code, workers.runs),
        (200, measurements),
        "status, and measurements by then",
    )


async def _measured() -> bytes:
    return b"{}"


def test_a_font_list_task_that_forgets_equals_a_fresh_one():
    async def kept_then_forgotten() -> _FontListTask:
        task = _FontListTask()
        await task.task(lambda: asyncio.create_task(_measured()))
        assert_every_field_filled(task, _FontListTask(), "the record once it keeps a list")
        task.forget()
        return task

    assert_equal(asyncio.run(kept_then_forgotten()), _FontListTask(), "the record forgotten")


async def _runs_out_of_time() -> bytes:
    raise TooSlow()


def test_a_font_list_task_that_forgets_a_failed_measuring_equals_a_fresh_one():
    async def failed_then_forgotten() -> _FontListTask:
        task = _FontListTask()
        measuring = task.task(lambda: asyncio.create_task(_runs_out_of_time()))
        with pytest.raises(TooSlow):
            await measuring
        assert_every_field_filled(task, _FontListTask(), "the record once a measuring failed")
        task.forget_if_failed(measuring)
        return task

    assert_equal(asyncio.run(failed_then_forgotten()), _FontListTask(), "the record forgotten")
