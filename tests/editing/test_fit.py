"""Will a replacement fit, and what to offer when it does not."""

from __future__ import annotations

from squidpdf.core import words
from squidpdf.editing import check
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_false, assert_in, assert_true


def test_check_reports_overflow_with_options(engine):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE)
    longer = span.text + " and"
    assert_equal(engine.missing(span, longer), [], "missing chars, isolating the width case")
    fit = check(engine, span, longer)
    assert_false(fit.ok, "fit.ok for text that overflows the line")
    assert_in("too long", fit.describe() or "", "the overflow description")
    offered = {o.name for o in fit.options}
    missing = {"shrink", "as-is"} - offered
    assert_true(not missing, f"options offered ({offered}) are missing {missing}")

    # A letter only the stand-in has, and one nothing has: both said, each its way.
    fit = check(engine, span, longer + " é →")
    assert_in("so the line is drawn in", fit.describe() or "", "the font switch")
    assert_in(words.WILL_LEAVE_OUT.format(letters="→"), fit.describe() or "", "the letter lost")


def test_check_is_quiet_when_nothing_is_wrong(engine):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE)
    fit = check(engine, span, "Delivery begins 2 March 2026")
    assert_true(fit.ok, "fit.ok for a replacement that fits cleanly")
    assert_true(fit.describe() is None, "describe() when nothing is wrong")
    assert_equal(fit.options, [], "options when nothing is wrong")


def test_past_the_shrink_floor_only_leave_it_long_is_offered(engine):
    index = engine.index()
    span = next(s for s in index if s.page == REFERENCED_PAGE and s.text.startswith("Made"))
    fit = check(engine, span, span.text * 2, "shrink")
    assert_equal([o.name for o in fit.options], ["as-is"], "options for twice the length")
    assert_equal(fit.strategy, "as-is", "the strategy drawn when shrink isn't offered")
    assert_in(words.NOT_OFFERED, fit.describe() or "", "why the choice wasn't used")
