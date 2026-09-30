"""PDF work runs in worker processes, and a task that hangs or overeats is killed."""

from __future__ import annotations

import asyncio
import sys
import time

import pymupdf
import pytest

from squidpdf.api.constants import WORKER_MEMORY_BYTES
from squidpdf.api.pool import Pool
from squidpdf.core import Problem
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


def _hang() -> None:
    """Work that takes far longer than any timeout here."""
    time.sleep(_HANG_S)


def _overeat() -> int:
    """Work that asks for twice a worker's memory."""
    return len(bytearray(2 * WORKER_MEMORY_BYTES))


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
    assert_true(waited < _ENOUGH_S, f"waited {waited:.1f} s for a {_TIMEOUT_S} s timeout")


@pytest.mark.skipif(sys.platform != "linux", reason="the memory ceiling is Linux only")
def test_work_past_the_memory_ceiling_is_called_too_heavy(pool):
    with pytest.raises(Problem) as caught:
        asyncio.run(pool.run(_ENOUGH_S, _overeat))
    assert_equal(caught.value.type, "too_heavy", "problem for a task past the memory ceiling")
