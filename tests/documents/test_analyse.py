"""Analysis runs once per build, always over the index built from the original."""

from __future__ import annotations

import shutil

import pytest

from squidpdf.core import Engine
from squidpdf.documents import analyse, store
from squidpdf.documents.constants import MAX_PAGES
from squidpdf.documents.errors import TooManyPages
from tests.conftest import drawn_with
from tests.helpers import assert_equal, assert_true


@pytest.fixture
def folder(pdf):
    """A stored document holding the sample as its original."""
    _, folder = store.create("owner")
    shutil.copy(pdf, folder / store.ORIGINAL)
    return folder


def test_a_new_build_judges_the_saved_index_never_a_new_one(folder, monkeypatch):
    first = analyse.analyse(str(folder), MAX_PAGES)

    def reindex(self):
        """Stands in for the engine's index, to fail if anything builds one."""
        raise AssertionError("the index was rebuilt")

    monkeypatch.setattr(Engine, "index", reindex)
    monkeypatch.setattr(analyse, "BUILD", "a-later-build")
    later = analyse.analyse(str(folder), MAX_PAGES)

    assert_equal(later["build"], "a-later-build", "build of the second analysis")
    first_ids = [s["id"] for s in first["spans"]]
    later_ids = [s["id"] for s in later["spans"]]
    assert_equal(later_ids, first_ids, "span ids across builds")
    kept = store.load_analysis(folder, "a-later-build")
    assert_true(kept is not None, "the later build's analysis wasn't kept")


def test_a_span_that_wont_come_back_as_it_looks_says_how_and_its_font_stays_usable():
    _, folder = store.create("owner")
    drawn_with(str(folder / store.ORIGINAL), setting="1.5 Tc")

    analysis = analyse.analyse(str(folder), MAX_PAGES)

    [span] = analysis["spans"]
    judged = (span["fidelity"], span["why"])
    assert_equal(judged, ("approximate", {"code": "spaced_text", "params": {}}), "the span")
    [font] = analysis["fonts"]
    assert_equal((font["substitute"], font["why"]), (None, None), "its font: nothing stands in")


def test_a_document_past_the_page_limit_is_refused_before_its_pages_are_read(
    folder, monkeypatch
):
    """Counted, not read: a huge file mustn't cost the reading it's refused to save."""

    def read_pages(self):
        """Stands in for reading every page's size, to fail if anything does."""
        raise AssertionError("the pages were read")

    monkeypatch.setattr(Engine, "pages", read_pages)
    with pytest.raises(TooManyPages):
        analyse.analyse(str(folder), 1)  # the sample has two
