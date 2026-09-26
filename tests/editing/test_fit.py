"""Will a replacement fit, and what to offer when it does not."""

from __future__ import annotations

from squidpdf.editing import check
from tests.helpers import assert_equal, assert_false, assert_in, assert_true


def test_check_reports_overflow_with_options(engine):
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    longer = span.text + " and"
    assert_equal(engine.missing(span, longer), [], "missing chars, isolating the width case")
    fit = check(engine, span, longer)
    assert_false(fit.ok, "fit.ok for text that overflows the line")
    assert_in("too long", fit.describe() or "", "the overflow description")
    offered = {o.name for o in fit.options}
    missing = {"shrink", "as-is"} - offered
    assert_true(not missing, f"options offered ({offered}) are missing {missing}")


def test_check_is_quiet_when_nothing_is_wrong(engine):
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    fit = check(engine, span, "Delivery begins 2 March 2026")
    assert_true(fit.ok, "fit.ok for a replacement that fits cleanly")
    assert_true(fit.describe() is None, "describe() when nothing is wrong")
    assert_equal(fit.options, [], "options when nothing is wrong")


def test_past_the_shrink_floor_only_leave_it_long_is_offered(engine):
    index = engine.index()
    span = next(s for s in index if s.page == 0 and s.text.startswith("Made"))
    fit = check(engine, span, span.text * 2, "shrink")
    assert_equal([o.name for o in fit.options], ["as-is"], "options for twice the length")
    assert_equal(fit.strategy, "as-is", "the strategy drawn when shrink isn't offered")
