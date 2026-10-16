"""The font list is measured once per server, however many browsers ask at once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

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

_BROWSERS = 2  # two first visits, at the same moment
# Long enough that every browser asks while the first measurement still runs.
_MEASURING_S = 0.2


class _StandInWorkers:
    """Workers that measure an empty font list after a moment, counting each measurement.

    Empty because these tests are about how often the list is measured, not
    what's in it: the real list is checked in test_documents.
    """

    def __init__(self, *, too_slow: int = 0) -> None:
        """Run out of time on the first `too_slow` measurements."""
        self.runs = 0
        self._too_slow = too_slow

    async def run(self, timeout: float, task: Callable[[], FontList]) -> FontList:
        """Count the measurement, then give an empty list, or run out of time."""
        self.runs += 1
        if self.runs <= self._too_slow:
            raise TooSlow()
        await asyncio.sleep(_MEASURING_S)
        return {"build": BUILD, "families": []}

    async def close(self) -> None:
        """Nothing to stop: no worker ever started."""


@pytest.fixture
def unlisted() -> Iterator[None]:
    """A server that hasn't listed its fonts yet; after, the stand-in's list is forgotten."""
    font_list._font_list_task.forget()
    yield
    font_list._font_list_task.forget()


async def _ask_at_once(app: FastAPI) -> list[httpx.Response]:
    """The font list, asked for by several browsers on one server at the same moment.

    Through one event loop, as the server runs: a TestClient gives each
    request a loop of its own.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as browser:
        asks = (browser.get("/api/fonts", params={"build": BUILD}) for _ in range(_BROWSERS))
        return await asyncio.gather(*asks)


def test_browsers_asking_for_the_font_list_at_once_wait_for_one_measurement(
    app, monkeypatch, unlisted
):
    workers = _StandInWorkers()
    monkeypatch.setattr(app.state, "pool", workers)

    replies = asyncio.run(_ask_at_once(app))

    assert_equal([reply.status_code for reply in replies], [200] * _BROWSERS, "statuses")
    assert_equal(workers.runs, 1, "measurements")


def test_a_font_list_that_ran_out_of_time_is_measured_again(
    app, browser, monkeypatch, unlisted
):
    """A slow start (a busy machine) mustn't leave the failure in place for good."""
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
    tmp_path, monkeypatch, unlisted, capsys, too_slow, logged, measurements
):
    """So the first browser after a deploy finds it measured, or under way, and starts none.

    One that fails is logged, since no request waits for it, and the first ask measures again.
    """
    workers = _StandInWorkers(too_slow=too_slow)
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    # Its own app, so its start runs here, with workers that count each measurement.
    monkeypatch.setattr(api_app, "start_pool", lambda: workers)
    with TestClient(api_app.create_app(), base_url=BASE_URL) as server:
        if server.portal is None:
            pytest.fail("the client hasn't started the app")
        # A turn of the app's loop: whatever its start began has begun by then.
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
    """A font list, measured at once: only that a task is kept matters here."""
    return b"{}"


def test_a_font_list_task_that_forgets_equals_a_fresh_one():
    """What a server keeps of its font list: forgotten, the next ask measures again."""

    async def kept_then_forgotten() -> _FontListTask:
        task = _FontListTask()
        await task.task(lambda: asyncio.create_task(_measured()))
        assert_every_field_filled(task, _FontListTask(), "the record once it keeps a list")
        task.forget()
        return task

    assert_equal(asyncio.run(kept_then_forgotten()), _FontListTask(), "the record forgotten")


async def _runs_out_of_time() -> bytes:
    """A measuring that fails, as one on a busy machine can."""
    raise TooSlow()


def test_a_font_list_task_that_forgets_a_failed_measuring_equals_a_fresh_one():
    """A measuring that failed is dropped, so the next request measures again."""

    async def failed_then_forgotten() -> _FontListTask:
        task = _FontListTask()
        measuring = task.task(lambda: asyncio.create_task(_runs_out_of_time()))
        with pytest.raises(TooSlow):
            await measuring
        assert_every_field_filled(task, _FontListTask(), "the record once a measuring failed")
        task.forget_if_failed(measuring)
        return task

    assert_equal(asyncio.run(failed_then_forgotten()), _FontListTask(), "the record forgotten")
