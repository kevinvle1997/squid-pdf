"""A document is a folder: what's kept comes back the same, and idle ones go."""

from __future__ import annotations

import os
import time

from squidpdf.documents import store
from squidpdf.documents.constants import IDLE_S
from tests.helpers import assert_equal, assert_false, assert_true


def test_a_saved_index_comes_back_span_for_span(engine):
    _, folder = store.create("owner")
    index = engine.index()
    store.save_index(folder, index)
    loaded = store.load_index(folder)
    assert_equal(list(loaded or []), list(index), "spans after a save and a load")


def test_the_sweeper_deletes_only_documents_idle_past_the_hour():
    _, idle = store.create("owner")
    _, fresh = store.create("owner")
    past = time.time() - IDLE_S - 1
    os.utime(idle, (past, past))
    store.sweep()
    assert_false(idle.exists(), "a document idle past the hour is still on disk")
    assert_true(fresh.exists(), "a document in use was swept")
