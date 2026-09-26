"""A document is a folder: what's kept comes back the same, and idle ones go."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from squidpdf.documents import api as documents
from squidpdf.documents import store
from squidpdf.documents.constants import IDLE_S
from squidpdf.documents.errors import Gone
from tests.helpers import assert_equal, assert_false, assert_in, assert_true

_A_MOMENT_S = 0.2  # many passes, when they're zero seconds apart


def test_a_saved_index_comes_back_span_for_span(engine):
    _, folder = store.create("owner")
    index = engine.index()
    store.save_index(folder, index)
    loaded = store.load_index(folder)
    assert_equal(list(loaded or []), list(index), "spans after a save and a load")


def test_touching_a_document_deleted_meanwhile_says_it_is_gone():
    """Found, then deleted by its owner before its clock restarts: gone, not a crash."""
    _, folder = store.create("owner")
    store.delete(folder)
    with pytest.raises(Gone):
        store.touch(folder)


def test_the_sweeper_deletes_only_documents_idle_past_the_hour():
    _, idle = store.create("owner")
    _, fresh = store.create("owner")
    past = time.time() - IDLE_S - 1
    os.utime(idle, (past, past))
    store.sweep()
    assert_false(idle.exists(), "a document idle past the hour is still on disk")
    assert_true(fresh.exists(), "a document in use was swept")


def test_a_document_deleted_during_a_sweep_does_not_stop_it(monkeypatch):
    """Its owner can delete it between the sweep listing it and reading its clock."""
    _, idle = store.create("owner")
    _, deleted = store.create("owner")
    past = time.time() - IDLE_S - 1
    os.utime(idle, (past, past))
    is_dir = Path.is_dir

    def deleted_right_after_the_check(folder: Path, **kwargs: bool) -> bool:
        found = is_dir(folder, **kwargs)
        if folder == deleted:
            store.delete(folder)  # the owner's delete lands here
        return found

    monkeypatch.setattr(Path, "is_dir", deleted_right_after_the_check)
    store.sweep()
    assert_false(idle.exists(), "a document idle past the hour is still on disk")


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

    async def sweep_for_a_moment() -> None:
        sweeping = asyncio.create_task(documents.sweep_forever())
        await asyncio.sleep(_A_MOMENT_S)
        sweeping.cancel()

    asyncio.run(sweep_for_a_moment())
    assert_true(passes > 1, f"sweeps after the one that failed: {passes - 1}")
    assert_in("the disk said no", caplog.text, "what the log says about the failed sweep")
