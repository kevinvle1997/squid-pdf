"""What a redaction of part of a span takes: whole words, and a span's runs merged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from squidpdf.core import new_text
from squidpdf.editing import BadRange, Redact
from squidpdf.editing.redacted_words import merged_words, words_named
from tests.helpers import assert_equal

# The cases the browser widens a selection and merges a span's runs by too
# (react/src/editing/redaction.test.ts, project.test.ts).
_CASES = json.loads(Path(__file__).with_name("redacted_words_cases.json").read_text())


@pytest.mark.parametrize("case", _CASES["widened"], ids=lambda case: case["what"])
def test_a_range_takes_whole_words_as_the_browser_widens_a_selection(case):
    """No client can make a redaction stop inside a word, which its hidden copies keep."""
    span = new_text(0, origin=(72.0, 100.0), text=case["text"], size=12.0, font="")
    redact = Redact("s", range=tuple(case["range"]))

    if case["words"] is None:
        with pytest.raises(BadRange):
            words_named(redact, span)
        return
    taken = words_named(redact, span)

    assert_equal([taken.start, taken.stop], case["words"], "the words it takes, by code point")


@pytest.mark.parametrize("case", _CASES["merged"], ids=lambda case: case["what"])
def test_a_spans_runs_of_words_merge_as_the_browser_merges_them(case):
    runs = merged_words([range(start, stop) for start, stop in case["ranges"]])

    assert_equal([[run.start, run.stop] for run in runs], case["runs"], "the runs")
