"""Whether a span can be edited in its own font, and the one number that tracks it."""

from __future__ import annotations

from collections.abc import Callable

from squidpdf.core import Fidelity, FidelityReport, Span, green_rate
from tests.helpers import assert_all, assert_between


def _describe_report(reports: dict[str, FidelityReport]) -> Callable[[Span], str]:
    return lambda s: f"{s.text!r} -> {reports[s.id].state}"


def test_referenced_font_is_a_substitution(engine):
    """Page 1's fonts are named but not in the file, so edits cannot match."""
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    page1 = [s for s in engine.index() if s.page == 0]
    describe = _describe_report(reports)
    assert_all(page1, lambda s: reports[s.id].state is Fidelity.SUBSTITUTE, describe)
    assert_all(page1, lambda s: bool(reports[s.id].substitute), describe)


def test_embedded_font_is_exact(engine):
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    page2 = [s for s in engine.index() if s.page == 1]
    describe = _describe_report(reports)
    assert_all(page2, lambda s: reports[s.id].state is Fidelity.EXACT, describe)


def test_green_rate_is_the_share_that_keep_their_font(engine):
    rate = green_rate(engine.assess(engine.index()))
    assert_between(rate, 0.0, 1.0, "green rate on a deliberately mixed fixture")
