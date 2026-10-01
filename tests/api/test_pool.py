"""PDF work runs in worker processes, and a task that hangs or overeats is killed."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import os
import signal
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pebble.pool.process
import pymupdf
import pytest
from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.api.constants import WORKER_MEMORY_BYTES, WORKERS
from squidpdf.api.pool import WorkerPool
from squidpdf.core import Problem
from squidpdf.editing.constants import EXPORT_TIMEOUT_S, RENDER_TIMEOUT_S
from tests.api.conftest import BASE_URL, upload
from tests.helpers import assert_at_most, assert_equal, assert_false, assert_true

_HANG_S = 60
_TIMEOUT_S = 0.5
_ENOUGH_S = 10
_BUSY_S = 5  # far past _TIMEOUT_S: a task that waited for these would be late
_WORK_S = 1  # how long a task works on after its caller has left
_POLL_S = 0.05


@pytest.fixture(scope="module")
def pool():
    """One pool of workers for the module, shut down after."""
    pool = WorkerPool()
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


def _kill_idle_worker(pool: WorkerPool) -> None:
    """Kill a worker between tasks, as the kernel's out-of-memory killer might."""
    idle_pid = asyncio.run(pool.run(_ENOUGH_S, os.getpid))
    os.kill(idle_pid, signal.SIGKILL)
    _wait_until(lambda: _is_gone(idle_pid), _ENOUGH_S)


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

    async def queued_behind_busy_workers(pool: WorkerPool) -> None:
        busy = [asyncio.ensure_future(pool.run(_BUSY_S, _hang)) for _ in range(WORKERS)]
        await asyncio.sleep(0)  # they take every worker first
        await pool.run(_TIMEOUT_S, _noop)
        for task in busy:
            task.cancel()

    pool = WorkerPool()  # its own: this one fills every worker
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
    pool = WorkerPool()  # its own: no worker started yet
    try:
        worker_pid = asyncio.run(pool.run(_TIMEOUT_S, os.getpid))
    finally:
        pool.close()
    assert_true(worker_pid != os.getpid(), "the task ran in a worker")


def _note_pid_then_work(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then says it's done."""
    (folder / "pid").write_text(str(os.getpid()))
    time.sleep(_WORK_S)
    (folder / "done").touch()


def _leave_while_it_works(pool: WorkerPool, folder: Path, timeout: float) -> int:
    """Start a task, leave once it's working, and wait until it's done or its worker gone.

    Returns the worker's process id.
    """

    async def leave() -> int:
        task = partial(_note_pid_then_work, folder)
        caller = asyncio.ensure_future(pool.run(timeout, task))
        while not (folder / "pid").exists():
            await asyncio.sleep(_POLL_S)
        caller.cancel()
        with contextlib.suppress(asyncio.CancelledError):  # raised: we cancelled it
            await caller
        worker_pid = int((folder / "pid").read_text())

        def ended() -> bool:
            return (folder / "done").exists() or _is_gone(worker_pid)

        # Inside the loop: when it ends, anything still running is cancelled.
        await asyncio.to_thread(_wait_until, ended, _ENOUGH_S)
        return worker_pid

    return asyncio.run(leave())


def test_a_render_whose_browser_left_finishes_and_keeps_its_worker(pool, tmp_path):
    """Stopping it would kill its worker, and the next task would wait for a new one."""
    worker_pid = _leave_while_it_works(pool, tmp_path, RENDER_TIMEOUT_S)

    assert_true((tmp_path / "done").exists(), "the render finished its work")
    assert_false(_is_gone(worker_pid), "the render's worker is still alive")


def test_an_export_whose_browser_left_is_stopped(pool, tmp_path):
    """Long enough that stopping it is worth a new worker."""
    worker_pid = _leave_while_it_works(pool, tmp_path, EXPORT_TIMEOUT_S)

    assert_true(_is_gone(worker_pid), "the export's worker was stopped")
    assert_false((tmp_path / "done").exists(), "the export didn't run to its end")


def _note_pid_then_die(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then crashes its worker."""
    (folder / "pid").write_text(str(os.getpid()))
    time.sleep(_WORK_S)
    os._exit(1)


def _note_pid_then_raise(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then fails as a bug of ours would."""
    (folder / "pid").write_text(str(os.getpid()))
    time.sleep(_WORK_S)
    raise ValueError("a bug in our own code")


async def _start_then_leave(
    pool: WorkerPool, folder: Path, task: Callable[[Path], None]
) -> int:
    """Start `task` as a render and leave once it's working. Returns its worker's process id."""
    caller = asyncio.ensure_future(pool.run(RENDER_TIMEOUT_S, partial(task, folder)))
    while not (folder / "pid").exists():
        await asyncio.sleep(_POLL_S)
    caller.cancel()
    with contextlib.suppress(asyncio.CancelledError):  # raised: we cancelled it
        await caller
    return int((folder / "pid").read_text())


def _asyncio_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What asyncio logged, once every finished task has been collected."""
    gc.collect()  # a task's unread error is logged when the task is collected
    return [record.getMessage() for record in caplog.records if record.name == "asyncio"]


def test_a_render_that_crashes_after_its_browser_left_logs_no_error(pool, tmp_path, caplog):
    """Nobody waits for its answer, and a crash is a file's doing: a log would be noise."""

    async def leave() -> None:
        worker_pid = await _start_then_leave(pool, tmp_path, _note_pid_then_die)
        await asyncio.to_thread(_wait_until, lambda: _is_gone(worker_pid), _ENOUGH_S)
        await asyncio.sleep(_WORK_S)  # for pebble to see it die and say so

    asyncio.run(leave())

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")


def test_a_render_that_hits_a_bug_after_its_browser_left_is_logged(pool, tmp_path, caplog):
    """Nobody waits for its answer, but a bug in our own code must still show in the log."""

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_raise)
        await asyncio.to_thread(_wait_until, lambda: bool(_asyncio_errors(caplog)), _ENOUGH_S)

    asyncio.run(leave())

    assert_equal(len(_asyncio_errors(caplog)), 1, "errors asyncio logged")


def test_closing_the_pool_under_a_render_whose_browser_left_logs_no_error(tmp_path, caplog):
    """The server stopping: nobody waits for that render's answer now."""

    async def leave_then_close(pool: WorkerPool) -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_work)
        pool.close()
        await asyncio.sleep(_WORK_S)  # for the render's end to come back

    pool = WorkerPool()  # its own: this closes it
    asyncio.run(leave_then_close(pool))

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")
