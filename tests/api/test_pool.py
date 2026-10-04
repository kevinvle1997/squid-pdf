"""PDF work runs in worker processes, and a task that hangs or overeats is killed."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import multiprocessing
import os
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from functools import partial
from pathlib import Path
from types import ModuleType

import pebble.pool.process
import pymupdf
import pytest
from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.api.constants import WORKER_MEMORY_BYTES, WORKERS
from squidpdf.api.pool import WorkerPool, start_pool
from squidpdf.core import Problem
from squidpdf.editing.constants import EXPORT_TIMEOUT_S, RENDER_TIMEOUT_S
from tests.api.conftest import BASE_URL, SERVER_PATH, upload
from tests.helpers import (
    assert_all,
    assert_at_least,
    assert_at_most,
    assert_equal,
    assert_false,
    assert_in,
    assert_not_in,
    assert_problem,
    assert_true,
)

_HANG_S = 60
_TIMEOUT_S = 0.5
_ENOUGH_S = 10
_BUSY_S = 5  # far past _TIMEOUT_S: a task that waited for these would be late
_WORK_S = 1  # how long a task works on after its caller has left
_POLL_S = 0.05


@pytest.fixture(scope="module")
def runner() -> Iterator[asyncio.Runner]:
    """One event loop for the module's tests: a pool works only in the loop it was made in."""
    with asyncio.Runner() as runner:
        yield runner


@pytest.fixture(scope="module")
def pool(runner: asyncio.Runner) -> Iterator[WorkerPool]:
    """One pool of workers for the module, made in its loop, shut down after."""
    pool = runner.run(_new_pool())
    yield pool
    runner.run(pool.close())


async def _new_pool() -> WorkerPool:
    """A pool made in the running loop, as the app's lifespan makes one."""
    return start_pool()


@contextlib.asynccontextmanager
async def _own_pool() -> AsyncIterator[WorkerPool]:
    """A pool of the test's own, made in the running loop and closed after."""
    pool = start_pool()
    try:
        yield pool
    finally:
        await pool.close()


def _hang() -> None:
    """Work that takes far longer than any timeout here."""
    time.sleep(_HANG_S)


def _overeat() -> int:
    """Work that asks for twice a worker's memory."""
    return len(bytearray(2 * WORKER_MEMORY_BYTES))


def _die() -> None:
    """Work whose process dies under it, as MuPDF crashing on a file takes it down."""
    os._exit(1)


def test_a_worker_that_dies_says_the_file_is_damaged(runner, pool):
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_ENOUGH_S, _die))
    assert_equal(caught.value.type, "damaged", "problem for a worker that died")


def _read_a_broken_font() -> None:
    """Work that fails inside MuPDF itself, with an error that holds a pointer."""
    pymupdf.Font(fontbuffer=b"not a font")


def test_a_failure_inside_the_pdf_library_comes_back_as_what_it_means(runner, pool):
    """Not a server error: MuPDF's own exception can't be sent back, its meaning can."""
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_ENOUGH_S, _read_a_broken_font))
    assert_equal(caught.value.type, "damaged", "problem for work MuPDF couldn't do")


def test_work_past_its_timeout_is_killed_and_called_too_slow(runner, pool):
    started = time.monotonic()
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_TIMEOUT_S, _hang))
    waited = time.monotonic() - started
    assert_equal(caught.value.type, "too_slow", "problem for a task that hung")
    assert_at_most(waited, _ENOUGH_S, f"seconds waited for a {_TIMEOUT_S} s timeout")


@pytest.mark.skipif(sys.platform != "linux", reason="the memory ceiling is Linux only")
def test_work_past_the_memory_ceiling_is_called_too_heavy(runner, pool):
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_ENOUGH_S, _overeat))
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


def _kill_idle_worker(client: TestClient, pool: WorkerPool) -> None:
    """Kill a worker between tasks, as the kernel's out-of-memory killer might.

    `client` started the app: its task runs in the app's loop, where the pool was made.
    """
    if client.portal is None:
        pytest.fail("the client hasn't started the app")
    idle_pid = client.portal.call(pool.run, _ENOUGH_S, os.getpid)
    os.kill(idle_pid, signal.SIGKILL)
    _wait_until(lambda: _is_gone(idle_pid), _ENOUGH_S)


def test_the_api_works_on_after_an_idle_worker_is_killed(tmp_path, monkeypatch, pdf_bytes):
    """Its own app: this breaks the pool on purpose."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    app = create_app()
    with TestClient(app, base_url=BASE_URL) as client:
        doc = upload(client, pdf_bytes).json()
        _kill_idle_worker(client, app.state.pool)
        page = client.get(
            f"/api/documents/{doc['id']}/pages/0", params={"scale": 1, "build": doc["build"]}
        )
        health = client.get("/api/health")

    assert_equal(page.status_code, 200, "page image status after a worker was killed")
    assert_equal(health.json(), {"status": "ok"}, "health after a worker was killed")


def test_a_task_sent_as_every_worker_dies_goes_again_on_a_new_pool():
    """pebble gives up on the pool while the task waits for a worker: it did no work yet.

    Every worker is killed and the task sent at once, before pebble looks: it
    looks a few times a second, and gives up on the pool when it finds a dead worker.
    """

    async def sent_as_every_worker_dies() -> tuple[set[int | None], int]:
        async with _own_pool() as pool:  # its own: this breaks it
            others = set(multiprocessing.active_children())
            await pool.run(_ENOUGH_S, _noop)  # every worker started
            workers = set(multiprocessing.active_children()) - others
            for worker in workers:
                worker.kill()
            return {worker.pid for worker in workers}, await pool.run(_ENOUGH_S, os.getpid)

    killed, worker_pid = asyncio.run(sent_as_every_worker_dies())

    assert_equal(len(killed), WORKERS, "workers killed")
    assert_not_in(worker_pid, killed, "the worker that ran the task")


_launch_process = pebble.pool.process.launch_process  # pebble's own, before a test patches it


def _cannot_start(*_args: object) -> None:
    """A machine with no room for another process."""
    raise OSError("no room for another process")


def _dies_as_it_starts(
    name: str, _function: object, daemon: bool, context: ModuleType, *_args: object
) -> multiprocessing.Process:
    """A worker that exits at once, as one does whose memory cap the host refuses."""
    return _launch_process(name, os._exit, daemon, context, 1)


@pytest.mark.parametrize(
    ("launch", "health_sees_it"),
    [
        pytest.param(_cannot_start, True, id="no worker can start"),
        # Health replaces the broken pool and finds the new one running: it can't tell.
        pytest.param(_dies_as_it_starts, False, id="every worker dies as it starts"),
    ],
)
def test_pdf_work_says_no_workers_when_no_worker_can_start(
    tmp_path, monkeypatch, caplog, pdf_bytes, launch, health_sees_it
):
    """Its own app, as it breaks the pool: the second page finds the new pool broken too."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    app = create_app()
    with TestClient(app, base_url=BASE_URL) as client:
        doc = upload(client, pdf_bytes).json()
        page = f"/api/documents/{doc['id']}/pages/0"
        params = {"scale": 1, "build": doc["build"]}
        _kill_idle_worker(client, app.state.pool)
        with monkeypatch.context() as machine:
            machine.setattr(pebble.pool.process, "launch_process", launch)
            broken_pages = [client.get(page, params=params) for _ in range(2)]
            broken_health = client.get("/api/health")
        healed_page = client.get(page, params=params)
        healed_health = client.get("/api/health")

    for broken_page in broken_pages:
        assert_problem(broken_page, "no_workers", 503)
    # The answer doesn't say why: the log does, for whoever runs the server.
    causes = [record for record in caplog.records if record.name == "squidpdf.api.pool"]
    assert_at_least(
        len(causes), len(broken_pages), "warnings the pool logged, one a page at least"
    )
    assert_all(causes, lambda record: record.exc_info is not None, lambda record: record.msg)
    if health_sees_it:
        assert_problem(broken_health, "no_workers", 503)
    assert_equal(healed_page.status_code, 200, "page image once workers can start again")
    assert_equal(healed_health.json(), {"status": "ok"}, "health once workers can start again")


def _noop() -> None:
    """Work that takes no time at all."""


def test_a_pool_works_only_in_the_event_loop_it_was_made_in(pool):
    """As the server runs one: asyncio ties a lock to the first loop that waits on it."""
    with pytest.raises(RuntimeError, match="no running event loop"):
        start_pool()
    with pytest.raises(RuntimeError, match="the event loop it was made in"):
        asyncio.run(pool.run(_ENOUGH_S, os.getpid))  # a loop of its own, not the pool's


def test_time_spent_waiting_for_a_worker_counts_toward_the_timeout():
    """Waiting behind other tasks is the caller's time, so it counts."""

    async def queued_behind_busy_workers() -> None:
        async with _own_pool() as pool:  # its own: this one fills every worker
            busy = [asyncio.ensure_future(pool.run(_BUSY_S, _hang)) for _ in range(WORKERS)]
            await asyncio.sleep(0)  # they take every worker first
            await pool.run(_TIMEOUT_S, _noop)
            for task in busy:
                task.cancel()

    started = time.monotonic()
    with pytest.raises(Problem) as caught:
        asyncio.run(queued_behind_busy_workers())
    waited = time.monotonic() - started
    assert_equal(caught.value.type, "too_slow", "problem for a task that never got a worker")
    assert_at_most(waited, _BUSY_S, "seconds waited, which the busy workers would have taken")


def test_a_new_workers_start_doesnt_count_toward_the_timeout():
    """Starting a worker is the server's time: a quick task on a new pool isn't too slow."""

    async def first_task() -> int:
        async with _own_pool() as pool:  # its own: no worker started yet
            return await pool.run(_TIMEOUT_S, os.getpid)

    worker_pid = asyncio.run(first_task())
    assert_true(worker_pid != os.getpid(), "the task ran in a worker")


def _note_pid(folder: Path) -> None:
    """Writes this worker's process id to `folder / "pid"`, whole.

    Written beside it, then renamed into place: the test waits for the file to
    exist, and `write_text` makes it empty before it fills it.
    """
    written = folder / "pid.partial"
    written.write_text(str(os.getpid()))
    written.replace(folder / "pid")


def _note_pid_then_work(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then says it's done."""
    _note_pid(folder)
    time.sleep(_WORK_S)
    (folder / "done").touch()


def _leave_while_it_works(
    runner: asyncio.Runner, pool: WorkerPool, folder: Path, timeout: float
) -> int:
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

    return runner.run(leave())


def test_a_render_whose_browser_left_finishes_and_keeps_its_worker(runner, pool, tmp_path):
    """Stopping it would kill its worker, and the next task would wait for a new one."""
    worker_pid = _leave_while_it_works(runner, pool, tmp_path, RENDER_TIMEOUT_S)

    assert_true((tmp_path / "done").exists(), "the render finished its work")
    assert_false(_is_gone(worker_pid), "the render's worker is still alive")


def test_an_export_whose_browser_left_is_stopped(runner, pool, tmp_path):
    """Long enough that stopping it is worth a new worker."""
    worker_pid = _leave_while_it_works(runner, pool, tmp_path, EXPORT_TIMEOUT_S)

    assert_true(_is_gone(worker_pid), "the export's worker was stopped")
    assert_false((tmp_path / "done").exists(), "the export didn't run to its end")


def _note_pid_then_die(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then crashes its worker."""
    _note_pid(folder)
    time.sleep(_WORK_S)
    os._exit(1)


def _note_pid_then_raise(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then fails as a bug of ours would."""
    _note_pid(folder)
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


def _pool_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What the pool itself logged."""
    return [
        record.getMessage() for record in caplog.records if record.name == "squidpdf.api.pool"
    ]


def _note_pid_then_read_a_broken_font(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then reads a broken font."""
    _note_pid(folder)
    time.sleep(_WORK_S)
    (folder / "done").touch()  # what the test waits for: the failure comes next
    _read_a_broken_font()


@pytest.mark.parametrize(
    "task",
    [
        pytest.param(_note_pid_then_die, id="its worker crashes"),
        pytest.param(_note_pid_then_read_a_broken_font, id="the file is damaged"),
    ],
)
def test_a_render_that_fails_on_its_file_after_its_browser_left_logs_no_error(
    runner, pool, tmp_path, caplog, task
):
    """Nobody waits for its answer, and the failure is the file's doing: a log is noise."""
    caplog.set_level(logging.WARNING, logger="squidpdf.api.pool")

    async def leave() -> None:
        worker_pid = await _start_then_leave(pool, tmp_path, task)

        def ended() -> bool:
            return (tmp_path / "done").exists() or _is_gone(worker_pid)

        await asyncio.to_thread(_wait_until, ended, _ENOUGH_S)
        await asyncio.sleep(_WORK_S)  # for pebble to see it end and say how

    runner.run(leave())

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")
    assert_equal(_pool_log(caplog), [], "what the pool logged")


def _note_pid_then_fail_on_the_server(folder: Path) -> None:
    """Writes down which worker runs it, works _WORK_S, then fails as a missing file does."""
    _note_pid(folder)
    time.sleep(_WORK_S)
    raise Problem(debug=f"FzErrorSystem: cannot open {SERVER_PATH}: No such file")


def test_a_render_that_fails_on_the_server_after_its_browser_left_logs_its_debug(
    runner, pool, tmp_path, caplog
):
    """Nobody waits for its answer, so the pool logs the debug the API's handler would have."""
    caplog.set_level(logging.WARNING, logger="squidpdf.api.pool")

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_fail_on_the_server)
        await asyncio.to_thread(_wait_until, lambda: bool(_pool_log(caplog)), _ENOUGH_S)

    runner.run(leave())

    said = "\n".join(_pool_log(caplog))
    assert_in(SERVER_PATH, said, "what the pool logged of it")
    assert_in("server_error", said, "the type the log gives it")
    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged: it's no bug")


def test_a_render_that_hits_a_bug_after_its_browser_left_is_logged(
    runner, pool, tmp_path, caplog
):
    """Nobody waits for its answer, but a bug in our own code must still show in the log."""

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_raise)
        await asyncio.to_thread(_wait_until, lambda: bool(_asyncio_errors(caplog)), _ENOUGH_S)

    runner.run(leave())

    assert_equal(len(_asyncio_errors(caplog)), 1, "errors asyncio logged")


def test_closing_the_pool_under_a_render_whose_browser_left_logs_no_error(tmp_path, caplog):
    """The server stopping: nobody waits for that render's answer now."""

    async def leave_then_close() -> None:
        pool = start_pool()  # its own: this closes it
        await _start_then_leave(pool, tmp_path, _note_pid_then_work)
        await pool.close()
        await asyncio.sleep(_WORK_S)  # for the render's end to come back

    asyncio.run(leave_then_close())

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")
