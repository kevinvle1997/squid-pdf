"""A document is a folder: what's kept comes back the same, and idle ones go."""

from __future__ import annotations

import asyncio
import os
import time

from squidpdf.documents import api as documents
from squidpdf.documents import store
from squidpdf.documents.constants import IDLE_S
from tests.helpers import assert_equal, assert_false, assert_in, assert_true

_PATIENCE_S = 10  # waited for a second pass; a slow machine needs far less


def test_an_id_that_climbs_out_of_the_store_finds_nothing(tmp_path):
    """The router refuses a slash before the store sees it; this is the store's own guard."""
    store.create("owner")  # the store's folder, where a climb would start
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "owner").write_text("anyone")  # shaped like a document
    assert_equal(store.find("../outside"), None, "a lookup that climbs out of the store")


def test_the_sweeper_deletes_only_documents_idle_past_the_hour():
    _, idle = store.create("owner")
    _, fresh = store.create("owner")
    past = time.time() - IDLE_S - 1
    os.utime(idle, (past, past))
    store.sweep()
    assert_false(idle.exists(), "a document idle past the hour is still on disk")
    assert_true(fresh.exists(), "a document in use was swept")


def test_a_failed_sweep_is_logged_and_sweeping_carries_on(monkeypatch, caplog):
    """Stopping would keep every document on disk from then on."""
    passes = 0

    def fails_the_first_time() -> None:
        nonlocal passes
        passes += 1
        if passes == 1:
            raise OSError("the disk said no")

    monkeypatch.setattr(documents, "SWEEP_EVERY_S", 0)
    monkeypatch.setattr(store, "sweep", fails_the_first_time)

    async def sweep_until_a_second_pass() -> None:
        """Sweep until a pass runs after the failed one, or patience runs out."""
        sweeping = asyncio.create_task(documents.sweep_forever())
        deadline = time.monotonic() + _PATIENCE_S
        # Waits on the passes themselves, not a fixed moment a slow runner can miss.
        while passes < 2 and not sweeping.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        sweeping.cancel()

    asyncio.run(sweep_until_a_second_pass())
    assert_true(passes > 1, f"sweeps after the one that failed: {passes - 1}")
    assert_in("the disk said no", caplog.text, "what the log says about the failed sweep")
