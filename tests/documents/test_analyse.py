"""Analysis runs once per build, always over the index built from the original."""

from __future__ import annotations

import shutil

import pytest

from squidpdf.core import Engine
from squidpdf.documents import analyse, store
from tests.helpers import assert_equal, assert_true


@pytest.fixture
def folder(pdf):
    """A stored document holding the sample as its original."""
    _, folder = store.create("owner")
    shutil.copy(pdf, folder / store.ORIGINAL)
    return folder


def test_a_new_build_judges_the_saved_index_never_a_new_one(folder, monkeypatch):
    first = analyse.analyse(str(folder))

    def reindex(self):
        """Stands in for the engine's index, to fail if anything builds one."""
        raise AssertionError("the index was rebuilt")

    monkeypatch.setattr(Engine, "index", reindex)
    monkeypatch.setattr(analyse, "BUILD", "a-later-build")
    later = analyse.analyse(str(folder))

    assert_equal(later["build"], "a-later-build", "build of the second analysis")
    first_ids = [s["id"] for s in first["spans"]]
    later_ids = [s["id"] for s in later["spans"]]
    assert_equal(later_ids, first_ids, "span ids across builds")
    kept = store.load_analysis(folder, "a-later-build")
    assert_true(kept is not None, "the later build's analysis wasn't kept")
