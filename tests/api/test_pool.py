"""PDF work runs in worker processes, and a task that hangs or overeats is killed."""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from squidpdf.api.constants import WORKER_MEMORY_BYTES
from squidpdf.api.errors import ApiError
from squidpdf.api.pool import Pool
from tests.helpers import assert_equal, assert_true

_HANG_S = 60
_TIMEOUT_S = 0.5
_ENOUGH_S = 10


@pytest.fixture(scope="module")
def pool():
    """One pool of workers for the module, shut down after."""
    pool = Pool()
    yield pool
    pool.close()


def _pid() -> int:
    """The process this runs in."""
    return os.getpid()


def _hang() -> None:
    """Work that takes far longer than any timeout here."""
    time.sleep(_HANG_S)


def _overeat() -> int:
    """Work that asks for twice a worker's memory."""
    return len(bytearray(2 * WORKER_MEMORY_BYTES))


def test_work_runs_in_a_worker_not_the_server(pool):
    worker = asyncio.run(pool.run(_ENOUGH_S, _pid))
    assert_true(worker != os.getpid(), f"worker pid {worker} is the server's own")


def test_work_past_its_timeout_is_killed_and_called_too_slow(pool):
    started = time.monotonic()
    with pytest.raises(ApiError) as caught:
        asyncio.run(pool.run(_TIMEOUT_S, _hang))
    waited = time.monotonic() - started
    assert_equal(caught.value.type, "too_slow", "problem for a task that hung")
    assert_true(waited < _ENOUGH_S, f"waited {waited:.1f} s for a {_TIMEOUT_S} s timeout")


@pytest.mark.skipif(sys.platform != "linux", reason="the memory ceiling is Linux only")
def test_work_past_the_memory_ceiling_is_called_too_heavy(pool):
    with pytest.raises(ApiError) as caught:
        asyncio.run(pool.run(_ENOUGH_S, _overeat))
    assert_equal(caught.value.type, "too_heavy", "problem for a task past the memory ceiling")
