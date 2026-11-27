"""At MuPDF's highest clean-up level every object is compared with every other."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf

from squidpdf.core import open_pdf
from tests.helpers import assert_at_most, assert_equal

_FEW_PAGES = 100
_MANY_PAGES = 300
_MOST_GROWTH = 5.0  # in step is 3; comparing every object with every other is about 9
_RUNS = 3  # the fastest of a few, so a busy machine doesn't count
_LINES = 45
_LEADING = 14.0
_TOP = 108.0
_LEFT = 72.0
_SIZE = 10.5


def _write_long(path: Path, pages: int) -> None:
    one = pymupdf.open()
    page = one.new_page()
    for line in range(_LINES):
        baseline = _TOP + _LEADING * line
        text = f"the provider shall deliver the services under clause {line}"
        page.insert_text((_LEFT, baseline), text, fontname="tiro", fontsize=_SIZE)
    long = pymupdf.open()
    for _ in range(pages):
        long.insert_pdf(one)
    long.save(path)
    long.close()
    one.close()


def _save_seconds(source: Path, out: Path) -> float:
    times = []
    for _ in range(_RUNS):
        with open_pdf(str(source)) as engine:
            start = time.process_time()
            engine.save(str(out))
            times.append(time.process_time() - start)
    return min(times)


def test_saving_three_times_the_pages_takes_about_three_times_as_long(tmp_path):
    few, many = tmp_path / "few.pdf", tmp_path / "many.pdf"
    _write_long(few, _FEW_PAGES)
    _write_long(many, _MANY_PAGES)
    out = tmp_path / "out.pdf"
    few_seconds = _save_seconds(few, out)
    growth = _save_seconds(many, out) / few_seconds
    assert_at_most(growth, _MOST_GROWTH, "save time at 3x the pages, as a multiple")
    with open_pdf(str(out)) as saved:
        assert_equal(saved.page_count(), _MANY_PAGES, "pages in the saved file")
