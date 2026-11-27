from __future__ import annotations

import asyncio
import contextlib
import errno
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

from squidpdf.api import constants
from squidpdf.api.constants import WORKER_MEMORY_BYTES, WORKERS
from squidpdf.api.pool import WorkerPool, start_pool
from squidpdf.core import (
    LogController,
    LogEvent,
    Problem,
    start_request,
    tally_fields,
)
from squidpdf.editing.constants import EXPORT_TIMEOUT_S, FONT_LIST_TIMEOUT_S, RENDER_TIMEOUT_S
from tests.api.conftest import DOCUMENT_ID, LOGGED_PATH, SERVER_PATH, upload
from tests.conftest import LOG_LINE, log_lines
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
_WORK_S = 1
_POLL_S = 0.05


@pytest.fixture(scope="module")
def runner() -> Iterator[asyncio.Runner]:
    with asyncio.Runner() as runner:
        yield runner


@pytest.fixture(scope="module")
def pool(runner: asyncio.Runner) -> Iterator[WorkerPool]:
    pool = runner.run(_new_pool())
    yield pool
    runner.run(pool.close())


async def _new_pool() -> WorkerPool:
    return start_pool()


@contextlib.asynccontextmanager
async def _own_pool() -> AsyncIterator[WorkerPool]:
    pool = start_pool()
    try:
        yield pool
    finally:
        await pool.close()


def _hang() -> None:
    time.sleep(_HANG_S)


def _overeat() -> int:
    return len(bytearray(2 * WORKER_MEMORY_BYTES))


def _die() -> None:
    os._exit(1)


def test_a_worker_that_dies_says_the_file_is_damaged(runner, pool):
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_ENOUGH_S, _die))
    assert_equal(caught.value.type, "damaged", "problem for a worker that died")


def _read_a_broken_font() -> None:
    pymupdf.Font(fontbuffer=b"not a font")


def test_a_failure_inside_the_pdf_library_comes_back_as_what_it_means(runner, pool):
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


def _return_too_much_to_send_back() -> bytes:
    return bytes(WORKER_MEMORY_BYTES * 3 // 5)


@pytest.mark.skipif(sys.platform != "linux", reason="the memory ceiling is Linux only")
@pytest.mark.parametrize(
    "task",
    [_overeat, _return_too_much_to_send_back],
    ids=["work that overeats", "a result too big to send back"],
)
def test_work_past_the_memory_ceiling_is_called_too_heavy(runner, pool, task):
    with pytest.raises(Problem) as caught:
        runner.run(pool.run(_ENOUGH_S, task))
    assert_equal(caught.value.type, "too_heavy", "problem for a task past the memory ceiling")


def _wait_until(done: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(_POLL_S)
    return done()


def _is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _kill_idle_worker(client: TestClient, pool: WorkerPool) -> None:
    if client.portal is None:
        pytest.fail("the client hasn't started the app")
    measured = _wait_until(lambda: not pool.jobs, FONT_LIST_TIMEOUT_S)
    assert_true(measured, f"the font list was measured within {FONT_LIST_TIMEOUT_S} s")
    idle_pid = client.portal.call(pool.run, _ENOUGH_S, os.getpid)
    os.kill(idle_pid, signal.SIGKILL)
    gone = _wait_until(lambda: _is_gone(idle_pid), _ENOUGH_S)
    assert_true(gone, f"the killed worker was reaped within {_ENOUGH_S} s")


def test_the_api_works_on_after_an_idle_worker_is_killed(own_app, pdf_bytes):
    with own_app.started() as client:
        doc = upload(client, pdf_bytes).json()
        _kill_idle_worker(client, own_app.app.state.pool)
        page = client.get(
            f"/api/documents/{doc['id']}/pages/0", params={"scale": 1, "build": doc["build"]}
        )
        health = client.get("/api/health")

    assert_equal(page.status_code, 200, "page image status after a worker was killed")
    assert_equal(health.json(), {"status": "ok"}, "health after a worker was killed")


def test_a_task_sent_as_every_worker_dies_goes_again_on_a_new_pool():
    async def sent_as_every_worker_dies() -> tuple[set[int | None], int]:
        async with _own_pool() as pool:
            others = set(multiprocessing.active_children())
            await pool.run(_ENOUGH_S, _noop)
            workers = set(multiprocessing.active_children()) - others
            for worker in workers:
                worker.kill()
            return {worker.pid for worker in workers}, await pool.run(_ENOUGH_S, os.getpid)

    killed, worker_pid = asyncio.run(sent_as_every_worker_dies())

    assert_equal(len(killed), WORKERS, "workers killed")
    assert_not_in(worker_pid, killed, "the worker that ran the task")


class _RefusesPickling:
    """Its pickling fails with neither of the two errors pebble's worker catches."""

    def __reduce__(self) -> str:
        raise ValueError("refuses pickling")


def _cannot_rebuild() -> None:
    raise RuntimeError("cannot rebuild")


class _PicklesButWontRebuild:
    def __reduce__(self) -> tuple[Callable[[], None], tuple[()]]:
        return _cannot_rebuild, ()


class _TwoPartFailure(Exception):
    """Unpickling calls it with its message alone, so it can't be read back."""

    def __init__(self, first: str, second: str) -> None:
        super().__init__(f"{first} {second}")


def _return_what_refuses_pickling() -> _RefusesPickling:
    return _RefusesPickling()


def _return_what_wont_rebuild() -> _PicklesButWontRebuild:
    return _PicklesButWontRebuild()


def _raise_what_wont_rebuild() -> None:
    raise _TwoPartFailure("our", "bug")


@pytest.mark.parametrize(
    "task",
    [_return_what_refuses_pickling, _return_what_wont_rebuild, _raise_what_wont_rebuild],
    ids=[
        "a result that can't be pickled",
        "a result that can't be read back",
        "a failure that can't be read back",
    ],
)
def test_an_answer_that_cannot_make_the_trip_back_is_our_bug_and_the_pool_works_on(task):
    async def sent_then_more() -> tuple[BaseException, float]:
        async with _own_pool() as pool:
            with pytest.raises(Exception) as caught:
                await pool.run(_ENOUGH_S, task)
            began = time.monotonic()
            await pool.run(_ENOUGH_S, _noop)
            return caught.value, time.monotonic() - began

    failure, next_took = asyncio.run(sent_then_more())

    assert_false(isinstance(failure, Problem), f"a Problem for our own bug: {failure!r}")
    assert_in("can't make the trip back", str(failure), "what the server error says")
    assert_at_most(next_took, _BUSY_S, "seconds the next task took")


_launch_process = pebble.pool.process.launch_process  # pebble's own, before a test patches it


def _cannot_start(*_args: object) -> None:
    raise OSError("no room for another process")


def _dies_as_it_starts(
    name: str, _function: object, daemon: bool, context: ModuleType, *_args: object
) -> multiprocessing.Process:
    return _launch_process(name, os._exit, daemon, context, 1)


def _no_thread(*_args: object) -> None:
    raise RuntimeError("can't start new thread")


def _no_channels(*_args: object) -> None:
    raise OSError(errno.EMFILE, "Too many open files")


@pytest.mark.parametrize(
    ("patched", "replacement", "health_sees_it"),
    [
        pytest.param("launch_process", _cannot_start, True, id="no worker can start"),
        # Health replaces the broken pool and finds the new one running: it can't tell.
        pytest.param(
            "launch_process", _dies_as_it_starts, False, id="every worker dies as it starts"
        ),
        pytest.param("channels", _no_channels, True, id="a new pool can't open its pipes"),
        pytest.param("launch_thread", _no_thread, True, id="a new pool can't start a thread"),
    ],
)
def test_pdf_work_says_no_workers_when_no_worker_can_start(
    own_app, monkeypatch, caplog, pdf_bytes, patched, replacement, health_sees_it
):
    with own_app.started() as client:
        doc = upload(client, pdf_bytes).json()
        page = f"/api/documents/{doc['id']}/pages/0"
        params = {"scale": 1, "build": doc["build"]}
        _kill_idle_worker(client, own_app.app.state.pool)
        with monkeypatch.context() as machine:
            machine.setattr(pebble.pool.process, patched, replacement)
            broken_pages = [client.get(page, params=params) for _ in range(2)]
            broken_health = client.get("/api/health")
        healed_page = client.get(page, params=params)
        healed_health = client.get("/api/health")

    for broken_page in broken_pages:
        assert_problem(broken_page, "no_workers", 503)
    causes = [record for record in caplog.records if record.name == "squidpdf.api.pool"]
    assert_at_least(
        len(causes), len(broken_pages), "warnings the pool logged, one a page at least"
    )
    assert_all(
        causes, lambda record: "Traceback" in record.getMessage(), lambda record: record.msg
    )
    if health_sees_it:
        assert_problem(broken_health, "no_workers", 503)
    assert_equal(healed_page.status_code, 200, "page image once workers can start again")
    assert_equal(healed_health.json(), {"status": "ok"}, "health once workers can start again")


def _noop() -> None: ...


def test_a_pool_works_only_in_the_event_loop_it_was_made_in(pool):
    with pytest.raises(RuntimeError, match="no running event loop"):
        start_pool()
    with pytest.raises(RuntimeError, match="the event loop it was made in"):
        asyncio.run(pool.run(_ENOUGH_S, os.getpid))


def test_time_spent_waiting_for_a_worker_counts_toward_the_timeout():
    async def queued_behind_busy_workers() -> tuple[Problem, float, dict[str, int]]:
        async with _own_pool() as pool:
            await pool.run(_ENOUGH_S, _noop)
            busy = [asyncio.ensure_future(pool.run(_BUSY_S, _hang)) for _ in range(WORKERS)]
            await asyncio.sleep(0)
            start_request()
            started = time.monotonic()
            with pytest.raises(Problem) as caught:
                await pool.run(_TIMEOUT_S, _noop)
            waited = time.monotonic() - started
            for task in busy:
                task.cancel()
            return caught.value, waited, tally_fields()

    problem, waited, tallied = asyncio.run(queued_behind_busy_workers())
    assert_equal(problem.type, "too_slow", "problem for a task that never got a worker")
    assert_at_most(waited, _BUSY_S / 2, "seconds waited, well short of the busy workers' time")
    assert_equal(list(tallied), ["queued_ms"], "what the request tallied")


def test_a_new_workers_start_doesnt_count_toward_the_timeout():
    async def first_task() -> int:
        async with _own_pool() as pool:
            return await pool.run(_TIMEOUT_S, os.getpid)

    worker_pid = asyncio.run(first_task())
    assert_true(worker_pid != os.getpid(), "the task ran in a worker")


def _log_a_line() -> None:
    # Named under the package, as every module is, so the server's handler writes it.
    LogController.for_module("squidpdf.tests.api.test_pool").write(
        LogEvent.GOOGLE_FETCH_LATE, timeout_s=2
    )


def test_a_line_logged_in_a_worker_has_the_servers_shape_and_its_requests_id(capfd):
    async def log_in_a_worker() -> str:
        request = start_request()
        async with _own_pool() as pool:
            await pool.run(_ENOUGH_S, _log_a_line)
        return request

    request = asyncio.run(log_in_a_worker())

    [line] = log_lines(capfd.readouterr().out)
    assert_true(LOG_LINE.fullmatch(line) is not None, f"{line!r} has the shape")
    expected = (
        f"WARN  skipped tests.api.test_pool google_fetch_late timeout_s=2 request={request}"
    )
    assert_in(expected, line, "the worker's line")


def _raise_a_bug() -> None:
    raise RuntimeError("a bug in the work")


def _tallied_by(tasks: list[Callable[[], object]]) -> list[dict[str, int]]:
    async def one_request_each() -> list[dict[str, int]]:
        tallied = []
        async with _own_pool() as pool:
            for task in tasks:
                start_request()
                with contextlib.suppress(Exception):
                    await pool.run(_ENOUGH_S, task)
                tallied.append(tally_fields())
        return tallied

    return asyncio.run(one_request_each())


def test_only_a_new_workers_first_task_is_tallied_as_cold(monkeypatch):
    monkeypatch.setattr(constants, "WORKERS", 1)

    first, second = _tallied_by([os.getpid, os.getpid])

    assert_equal((first.get("cold"), second.get("cold")), (1, None), "cold, by request")


@pytest.mark.parametrize("fail", [_read_a_broken_font, _raise_a_bug], ids=["problem", "bug"])
def test_a_task_that_fails_still_sends_back_what_it_tallied(monkeypatch, fail):
    monkeypatch.setattr(constants, "WORKERS", 1)

    [tallied] = _tallied_by([fail])

    assert_equal(sorted(tallied), ["cold", "queued_ms", "worker_ms"], "what it tallied")


def _note_pid(folder: Path) -> None:
    """Renamed into place: the test waits for the file, and `write_text` empties it first."""
    written = folder / "pid.partial"
    written.write_text(str(os.getpid()))
    written.replace(folder / "pid")


def _note_pid_then_work(folder: Path) -> None:
    _note_pid(folder)
    time.sleep(_WORK_S)
    (folder / "done").touch()


def _leave_while_it_works(
    runner: asyncio.Runner, pool: WorkerPool, folder: Path, timeout: float
) -> tuple[int, bool]:
    async def leave() -> tuple[int, bool]:
        task = partial(_note_pid_then_work, folder)
        caller = asyncio.ensure_future(pool.run(timeout, task))
        while not (folder / "pid").exists():
            await asyncio.sleep(_POLL_S)
        caller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await caller
        done_when_let_go = (folder / "done").exists()
        worker_pid = int((folder / "pid").read_text())

        def ended() -> bool:
            return (folder / "done").exists() or _is_gone(worker_pid)

        # Inside the loop: when it ends, anything still running is cancelled.
        await asyncio.to_thread(_wait_until, ended, _ENOUGH_S)
        return worker_pid, done_when_let_go

    return runner.run(leave())


def test_a_render_whose_browser_left_finishes_and_keeps_its_worker(runner, pool, tmp_path):
    worker_pid, done_when_let_go = _leave_while_it_works(
        runner, pool, tmp_path, RENDER_TIMEOUT_S
    )

    assert_true(done_when_let_go, "the render had finished its work as its caller was let go")
    assert_false(_is_gone(worker_pid), "the render's worker is still alive")


def test_an_export_whose_browser_left_is_stopped(runner, pool, tmp_path):
    worker_pid, _done_when_let_go = _leave_while_it_works(
        runner, pool, tmp_path, EXPORT_TIMEOUT_S
    )

    assert_true(_is_gone(worker_pid), "the export's worker was stopped")
    assert_false((tmp_path / "done").exists(), "the export didn't run to its end")


def _note_pid_then_die(folder: Path) -> None:
    _note_pid(folder)
    time.sleep(_WORK_S)
    os._exit(1)


def _note_pid_then_raise(folder: Path) -> None:
    _note_pid(folder)
    time.sleep(_WORK_S)
    raise ValueError("a bug in our own code")


async def _start_then_leave(
    pool: WorkerPool, folder: Path, task: Callable[[Path], None]
) -> int:
    caller = asyncio.ensure_future(pool.run(RENDER_TIMEOUT_S, partial(task, folder)))
    while not (folder / "pid").exists():
        await asyncio.sleep(_POLL_S)
    caller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await caller
    return int((folder / "pid").read_text())


async def _wait_until_every_task_ends(pool: WorkerPool) -> None:
    deadline = time.monotonic() + _ENOUGH_S
    while pool.jobs:
        if time.monotonic() > deadline:
            pytest.fail(f"a task still running {_ENOUGH_S} s after its caller left")
        await asyncio.sleep(_POLL_S)
    await asyncio.sleep(0)


def _asyncio_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    gc.collect()  # a task's unread error is logged when the task is collected
    return [record.getMessage() for record in caplog.records if record.name == "asyncio"]


def _pool_log(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records if record.name == "squidpdf.api.pool"
    ]


def _note_pid_then_read_a_broken_font(folder: Path) -> None:
    _note_pid(folder)
    time.sleep(_WORK_S)
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
    caplog.set_level(logging.WARNING, logger="squidpdf.api.pool")

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, task)
        await _wait_until_every_task_ends(pool)

    runner.run(leave())

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")
    assert_equal(_pool_log(caplog), [], "what the pool logged")


def _note_pid_then_fail_on_the_server(folder: Path) -> None:
    _note_pid(folder)
    time.sleep(_WORK_S)
    raise Problem(debug=f"FzErrorSystem: cannot open {SERVER_PATH}: No such file")


def test_a_render_that_fails_on_the_server_after_its_browser_left_logs_its_debug(
    runner, pool, tmp_path, caplog
):
    caplog.set_level(logging.WARNING, logger="squidpdf.api.pool")

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_fail_on_the_server)
        await asyncio.to_thread(_wait_until, lambda: bool(_pool_log(caplog)), _ENOUGH_S)

    runner.run(leave())

    said = "\n".join(_pool_log(caplog))
    assert_in(LOGGED_PATH, said, "what the pool logged of it")
    assert_not_in(DOCUMENT_ID, said, "the document's id, in the log")
    assert_in("server_error", said, "the type the log gives it")
    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged: it's no bug")


def test_a_render_that_hits_a_bug_after_its_browser_left_is_logged(
    runner, pool, tmp_path, caplog
):
    caplog.set_level(logging.WARNING, logger="squidpdf.api.pool")

    async def leave() -> None:
        await _start_then_leave(pool, tmp_path, _note_pid_then_raise)
        await asyncio.to_thread(_wait_until, lambda: bool(_pool_log(caplog)), _ENOUGH_S)

    runner.run(leave())

    errors = [
        record.getMessage() for record in caplog.records if record.levelno == logging.ERROR
    ]
    [said] = errors
    line, traceback_text = said.split("\n", 1)
    assert_equal(line, "failed  api.pool task_failed_caller_left", "the error the pool logged")
    assert_in("Traceback", traceback_text, "the bug's traceback, after its line")
    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged: the pool said it")


def test_closing_the_pool_under_a_render_whose_browser_left_logs_no_error(tmp_path, caplog):
    async def leave_then_close() -> None:
        pool = start_pool()
        await _start_then_leave(pool, tmp_path, _note_pid_then_work)
        await pool.close()
        await _wait_until_every_task_ends(pool)

    asyncio.run(leave_then_close())

    assert_equal(_asyncio_errors(caplog), [], "errors asyncio logged")
