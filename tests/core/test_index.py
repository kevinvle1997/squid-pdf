"""Extraction: finding spans, and keeping their ids stable."""

from __future__ import annotations

from squidpdf.core import MuPDFEngine
from tests.helpers import assert_any, assert_equal, assert_true


def test_index_finds_text(engine):
    index = engine.index()
    assert_true(len(index) >= 3, f"expected at least 3 spans, found {len(index)}")
    assert_any(index, lambda s: "14 March 2026" in s.text, describe=lambda s: repr(s.text))


def test_span_ids_are_stable_across_reindexing(pdf):
    """The client holds these. They must not move."""
    with MuPDFEngine(pdf) as a, MuPDFEngine(pdf) as b:
        assert_equal(
            [s.id for s in a.index()],
            [s.id for s in b.index()],
            "span ids across two indexes of the same pristine document",
        )
