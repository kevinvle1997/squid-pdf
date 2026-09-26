"""Analysis runs once per build, always over the index built from the original."""

from __future__ import annotations

import shutil

import orjson
import pytest

from squidpdf.api.constants import MAX_PAGES
from squidpdf.core import BUILD, Engine, words
from squidpdf.documents import analyse, store
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


def test_what_is_kept_is_in_no_language_so_any_can_say_it(folder):
    analyse.analyse(str(folder), MAX_PAGES)
    kept = store.load_analysis(folder, BUILD)
    assert_true(kept is not None, "the analysis wasn't kept")
    fonts = {font["name"]: font for font in orjson.loads(kept or b"")["fonts"]}
    why = {"code": "font_not_in_file", "params": {}}
    assert_equal(fonts["Times-Roman"]["why"], why, "why Times' own copy can't be used")
    assert_true(
        words.FONT_NOT_IN_FILE.encode() not in (kept or b""), "an English sentence kept"
    )


def test_an_analysis_kept_in_words_is_worked_out_again(folder):
    """One kept before sentences were codes has English in it, and the edge can't say that."""
    (folder / f"analysis-{BUILD}.json").write_bytes(b'{"fonts": [{"why": "In English."}]}')
    assert_true(store.load_analysis(folder, BUILD) is None, "the old analysis was read")
