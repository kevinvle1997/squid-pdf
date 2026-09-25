"""Analysis runs once per build, always over the index built from the original."""

from __future__ import annotations

import shutil

import pytest

from squidpdf.core import MuPDFEngine
from squidpdf.documents import analyse, store
from tests.helpers import assert_equal, assert_true


@pytest.fixture
def folder(pdf):
    _, folder = store.create("owner")
    shutil.copy(pdf, folder / store.ORIGINAL)
    return folder


def test_a_new_build_judges_the_saved_index_never_a_new_one(folder, monkeypatch):
    first = analyse.analyse(str(folder))

    def reindex(self):
        raise AssertionError("the index was rebuilt")

    monkeypatch.setattr(MuPDFEngine, "index", reindex)
    monkeypatch.setattr(analyse, "BUILD", "a-later-build")
    later = analyse.analyse(str(folder))

    assert_equal(later["build"], "a-later-build", "build of the second analysis")
    ids = [[s["id"] for s in a["spans"]] for a in (first, later)]
    assert_equal(ids[1], ids[0], "span ids across builds")
    kept = store.load_analysis(folder, "a-later-build")
    assert_true(kept is not None, "the later build's analysis wasn't kept")
