from __future__ import annotations

import json
from pathlib import Path

import pytest

from squidpdf.core import new_text
from squidpdf.editing import BadRange, Redact
from squidpdf.editing.redacted_words import merged_ranges, range_of
from tests.helpers import assert_equal

# The browser's tests read these cases too.
_CASES = json.loads(Path(__file__).with_name("redacted_words_cases.json").read_text())


@pytest.mark.parametrize("case", _CASES["widened"], ids=lambda case: case["what"])
def test_a_range_takes_whole_words_as_the_browser_widens_a_selection(case):
    span = new_text(0, origin=(72.0, 100.0), text=case["text"], size=12.0, font="")
    redact = Redact("s", range=tuple(case["range"]))

    if case["widened"] is None:
        with pytest.raises(BadRange):
            range_of(redact, span)
        return
    taken = range_of(redact, span)

    assert_equal(
        [taken.start, taken.stop], case["widened"], "the words it takes, by code point"
    )


@pytest.mark.parametrize("case", _CASES["merged"], ids=lambda case: case["what"])
def test_a_spans_ranges_of_words_merge_as_the_browser_merges_them(case):
    merged = merged_ranges([range(start, stop) for start, stop in case["ranges"]])

    assert_equal([[each.start, each.stop] for each in merged], case["merged"], "the ranges")
