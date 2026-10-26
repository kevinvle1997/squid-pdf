"""Will a replacement fit, and what to offer when it does not."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from squidpdf.core import (
    CONDENSE_LIMIT,
    OPTION_KEYS,
    ROOM_SLACK_PT,
    SHRINK_FLOOR,
    TOLERANCE_PT,
    Message,
    QuarterTurn,
    open_pdf,
    reason_of,
    words,
)
from squidpdf.editing import FitReport, Strategy, options_for, replace_fit
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE, in_a_copy_lacking
from tests.helpers import assert_equal, assert_false, assert_in, assert_true


def test_check_reports_overflow_with_options(engine):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE)
    longer = span.text + " and"
    assert_equal(
        engine.fit.plan_for(span, longer).missing, [], "missing chars, isolating the width case"
    )
    fit = replace_fit(engine, span, longer, room_pt=0.0).report
    assert_false(fit.ok, "fit.ok for text that overflows the line")
    assert_in("too long", words.render_all(fit.describe()) or "", "the overflow description")
    offered = {o.name for o in fit.options}
    missing = {"shrink", "as-is"} - offered
    assert_true(not missing, f"options offered ({offered}) are missing {missing}")

    # A letter only the substitute has, and one nothing has: both said, each its way.
    fit = replace_fit(engine, span, longer + " é 中", room_pt=0.0).report
    switch = words.sentence("missing").format(chars="é", font="Liberation Serif Regular")
    assert_in(
        switch,
        words.render_all(fit.describe()) or "",
        "the font switch, naming the face that draws",
    )
    assert_in(
        words.sentence("will_leave_out").format(letters="中"),
        words.render_all(fit.describe()) or "",
        "the letter lost",
    )


def test_check_is_quiet_when_nothing_is_wrong(engine):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE)
    fit = replace_fit(engine, span, "Delivery begins 2 March 2026", room_pt=0.0).report
    assert_true(fit.ok, "fit.ok for a replacement that fits cleanly")
    assert_equal(fit.describe(), [], "describe() when nothing is wrong")
    assert_equal(fit.options, [], "options when nothing is wrong")


def test_past_the_shrink_floor_only_leave_it_long_is_offered(engine):
    index = engine.index()
    span = next(s for s in index if s.page == REFERENCED_PAGE and s.text.startswith("Made"))
    fit = replace_fit(engine, span, span.text * 2, room_pt=0.0, strategy="shrink").report
    assert_equal([o.name for o in fit.options], ["as-is"], "options for twice the length")
    assert_equal(fit.strategy, "as-is", "the strategy drawn when shrink isn't offered")
    assert_in(
        words.sentence("not_offered"),
        words.render_all(fit.describe()) or "",
        "why the choice wasn't used",
    )


def test_every_way_out_has_its_sentences():
    """The browser sends a strategy by name, and its label and detail are keyed by it."""
    assert_equal(set(get_args(Strategy.__value__)), set(OPTION_KEYS), "the ways out")


# The cases the browser's fit check is tested on too (react/src/editing/fit.test.ts).
_SHARED = json.loads(Path(__file__).with_name("fit_cases.json").read_text())


def test_the_shared_cases_use_the_servers_rules():
    """The browser gets these rules from the server; its test reads them from the cases."""
    rules = {
        "tolerance_pt": TOLERANCE_PT,
        "room_slack_pt": ROOM_SLACK_PT,
        "condense_limit": CONDENSE_LIMIT,
        "shrink_floor": SHRINK_FLOOR,
    }
    assert_equal(_SHARED["rules"], rules, "the rules the shared cases are judged by")


@pytest.mark.parametrize("case", _SHARED["cases"], ids=lambda case: case["what"])
def test_a_line_is_too_long_only_past_its_room_as_the_browser_says(case):
    """Its room is the free space after it on its line; the warning says how far past it."""
    report = FitReport(
        delta_pt=case["delta_pt"], room_pt=case["room_pt"], original_pt=case["original_pt"]
    )
    options = options_for(case["delta_pt"], case["original_pt"], room_pt=case["room_pt"])

    percent = case["too_long_percent"]
    said = [] if percent is None else [Message("too_long", {"percent": percent})]
    assert_equal(report.describe(), said, "what's said of the length")
    assert_equal(report.ok, percent is None, "whether it fits")
    assert_equal([option.name for option in options], case["options"], "the ways out")


_TURNED: QuarterTurn = 90  # a `turned` case's line, turned counterclockwise: up the page


@pytest.mark.parametrize("case", _SHARED["verdicts"], ids=lambda case: case["what"])
def test_an_edits_verdict_is_the_one_the_browser_works_out_as_it_is_typed(case, tmp_path):
    """Upload judges a span on its own words; an edit is judged on the words typed."""
    path = in_a_copy_lacking(
        str(tmp_path / "case.pdf"),
        text=case["text"],
        lacking=case["lacking"],
        stored=case["stored"],
        turn_ccw=_TURNED if case["turned"] else 0,
    )
    with open_pdf(path) as engine:
        [span] = engine.index()
        [report] = engine.assess(engine.index())
        verdict = replace_fit(engine, span, case["typed"], room_pt=0.0).verdict

    # As the browser gets it: a reason only where its own font draws it unlike the page.
    approximate_why = report.why if report.state == "approximate" else None
    why = None if approximate_why is None else reason_of(approximate_why)
    judged = {"fidelity": report.state, "why": why}
    assert_equal(
        judged, case["span"], "the span, judged on its own words, as the browser gets it"
    )
    assert_equal(report.in_file, case["stored"], "whether the file's own copy can draw it")
    assert_equal(verdict.fidelity, case["fidelity"], "the edit's verdict")
