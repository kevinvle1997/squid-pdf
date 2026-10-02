"""The font list is measured once per server, however many browsers ask at once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI

from squidpdf.api.errors import TooSlow
from squidpdf.core import BUILD
from squidpdf.editing import font_list
from squidpdf.editing.font_list import FontListTasks
from squidpdf.editing.types import FontList
from tests.api.conftest import BASE_URL
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


@pytest.fixture
def unlisted() -> Iterator[None]:
    """A server that hasn't listed its fonts yet; after, the stand-in's list is forgotten."""
    font_list.font_list_tasks.forget()
    yield
    font_list.font_list_tasks.forget()


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


async def _measured() -> bytes:
    """A font list, measured at once: only that a task is kept matters here."""
    return b"{}"


def test_font_list_tasks_that_forget_equal_fresh_ones():
    """What a server keeps of its font list: forgotten, the next ask measures again."""

    async def kept_then_forgotten() -> FontListTasks:
        tasks = FontListTasks()
        await tasks.task_for(BUILD, lambda: asyncio.create_task(_measured()))
        assert_every_field_filled(tasks, FontListTasks(), "the record once it keeps a list")
        tasks.forget()
        return tasks

    assert_equal(asyncio.run(kept_then_forgotten()), FontListTasks(), "the record forgotten")


async def _runs_out_of_time() -> bytes:
    """A measuring that fails, as one on a busy machine can."""
    raise TooSlow()


def test_font_list_tasks_that_forget_a_failed_measuring_equal_fresh_ones():
    """A measuring that failed is dropped, so the next request measures again."""

    async def failed_then_forgotten() -> FontListTasks:
        tasks = FontListTasks()
        measuring = tasks.task_for(BUILD, lambda: asyncio.create_task(_runs_out_of_time()))
        with pytest.raises(TooSlow):
            await measuring
        assert_every_field_filled(tasks, FontListTasks(), "the record once a measuring failed")
        tasks.forget_if_failed(BUILD, measuring)
        return tasks

    assert_equal(asyncio.run(failed_then_forgotten()), FontListTasks(), "the record forgotten")
