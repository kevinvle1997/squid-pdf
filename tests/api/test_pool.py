"""PDF work runs in worker processes, and a task that hangs or overeats is killed."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from collections.abc import Callable

import pebble.pool.process
import pymupdf
import pytest
from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.api.constants import WORKER_MEMORY_BYTES, WORKERS
from squidpdf.api.pool import Pool
from squidpdf.core import Problem
from tests.api.conftest import BASE_URL, upload
from tests.helpers import assert_at_most, assert_equal, assert_true

_HANG_S = 60
_TIMEOUT_S = 0.5
_ENOUGH_S = 10
_BUSY_S = 5  # far past _TIMEOUT_S: a task that waited for these would be late
_POLL_S = 0.05


@pytest.fixture(scope="module")
def pool():
    """One pool of workers for the module, shut down after."""
    pool = Pool()
    yield pool
    pool.close()


def _hang() -> None:
    """Work that takes far longer than any timeout here."""
    time.sleep(_HANG_S)


def _overeat() -> int:
    """Work that asks for twice a worker's memory."""
    return len(bytearray(2 * WORKER_MEMORY_BYTES))


def _die() -> None:
    """Work whose process dies under it, as MuPDF crashing on a file takes it down."""
    os._exit(1)


def test_a_worker_that_dies_says_the_file_is_damaged(pool):
    with pytest.raises(Problem) as caught:
        asyncio.run(pool.run(_ENOUGH_S, _die))
    assert_equal(caught.value.type, "damaged", "problem for a worker that died")


def _read_a_broken_font() -> None:
    """Work that fails inside MuPDF itself, with an error that holds a pointer."""
    pymupdf.Font(fontbuffer=b"not a font")


def test_a_failure_inside_the_pdf_library_comes_back_as_what_it_means(pool):
    """Not a server error: MuPDF's own exception can't be sent back, its meaning can."""
    with pytest.raises(Problem) as caught:
        asyncio.run(pool.run(_ENOUGH_S, _read_a_broken_font))
    assert_equal(caught.value.type, "damaged", "problem for work MuPDF couldn't do")


def test_work_past_its_timeout_is_killed_and_called_too_slow(pool):
    started = time.monotonic()
    with pytest.raises(Problem) as caught:
        asyncio.run(pool.run(_TIMEOUT_S, _hang))
    waited = time.monotonic() - started
    assert_equal(caught.value.type, "too_slow", "problem for a task that hung")
    assert_at_most(waited, _ENOUGH_S, f"seconds waited for a {_TIMEOUT_S} s timeout")


@pytest.mark.skipif(sys.platform != "linux", reason="the memory ceiling is Linux only")
def test_work_past_the_memory_ceiling_is_called_too_heavy(pool):
    with pytest.raises(Problem) as caught:
        asyncio.run(pool.run(_ENOUGH_S, _overeat))
    assert_equal(caught.value.type, "too_heavy", "problem for a task past the memory ceiling")


def _wait_until(done: Callable[[], bool], seconds: float) -> bool:
    """Whether `done()` came true within `seconds`, asked every _POLL_S."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(_POLL_S)
    return done()


def _is_gone(pid: int) -> bool:
    """Whether process `pid` has ended and been reaped, as pebble does when it notices."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # raised once no such process exists
        return True
    return False


def _kill_idle_worker(pool: Pool) -> None:
    """Kill a worker between tasks, as the kernel's out-of-memory killer might."""
    idle = asyncio.run(pool.run(_ENOUGH_S, os.getpid))
    os.kill(idle, signal.SIGKILL)
    _wait_until(lambda: _is_gone(idle), _ENOUGH_S)


def test_the_api_works_on_after_an_idle_worker_is_killed(tmp_path, monkeypatch, pdf_bytes):
    """Its own app: this breaks the pool on purpose."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    app = create_app()
    with TestClient(app, base_url=BASE_URL) as client:
        doc = upload(client, pdf_bytes).json()
        _kill_idle_worker(app.state.pool)
        page = client.get(
            f"/api/documents/{doc['id']}/pages/0", params={"scale": 1, "build": doc["build"]}
        )
        health = client.get("/api/health")

    assert_equal(page.status_code, 200, "page image status after a worker was killed")
    assert_equal(health.json(), {"status": "ok"}, "health after a worker was killed")


def _cannot_start(*_args: object) -> None:
    """A machine with no room for another process."""
    raise OSError("no room for another process")


def test_health_says_so_when_no_worker_can_start(tmp_path, monkeypatch):
    """Its own app: this breaks the pool on purpose."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    app = create_app()
    with TestClient(app, base_url=BASE_URL) as client:
        _kill_idle_worker(app.state.pool)
        with monkeypatch.context() as machine:
            machine.setattr(pebble.pool.process, "launch_process", _cannot_start)
            broken = client.get("/api/health")
        healed = client.get("/api/health")

    assert_equal(broken.status_code, 503, "health status while no worker can start")
    assert_equal(broken.json(), {"status": "no_workers"}, "health while no worker can start")
    assert_equal(healed.json(), {"status": "ok"}, "health once workers can start again")


def _noop() -> None:
    """Work that takes no time at all."""


def test_time_spent_waiting_for_a_worker_counts_toward_the_timeout():
    """Waiting behind other tasks is the caller's time, so it counts."""

    async def queued_behind_busy_workers(pool: Pool) -> None:
        busy = [asyncio.ensure_future(pool.run(_BUSY_S, _hang)) for _ in range(WORKERS)]
        await asyncio.sleep(0)  # they take every worker first
        await pool.run(_TIMEOUT_S, _noop)
        for task in busy:
            task.cancel()

    pool = Pool()  # its own: this one fills every worker
    started = time.monotonic()
    try:
        with pytest.raises(Problem) as caught:
            asyncio.run(queued_behind_busy_workers(pool))
        waited = time.monotonic() - started
    finally:
        pool.close()
    assert_equal(caught.value.type, "too_slow", "problem for a task that never got a worker")
    assert_at_most(waited, _BUSY_S, "seconds waited, which the busy workers would have taken")


def test_a_new_workers_start_doesnt_count_toward_the_timeout():
    """Starting a worker is the server's time: a quick task on a new pool isn't too slow."""
    pool = Pool()  # its own: no worker started yet
    try:
        worker = asyncio.run(pool.run(_TIMEOUT_S, os.getpid))
    finally:
        pool.close()
    assert_true(worker != os.getpid(), "the task ran in a worker")
