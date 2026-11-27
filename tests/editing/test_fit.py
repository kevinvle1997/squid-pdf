from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import orjson
import pytest

from squidpdf.core import (
    OPTION_KEYS,
    Message,
    QuarterTurn,
    open_pdf,
    reason_of,
    words,
)
from squidpdf.documents.replies import document_json
from squidpdf.documents.types import AnalysisFacts, KeptAnalysis
from squidpdf.editing import (
    FitReport,
    RunWidths,
    Strategy,
    options_for,
    replace_fit,
    runs_laid_out,
)
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE, in_a_copy_lacking
from tests.helpers import assert_close, assert_equal, assert_false, assert_in, assert_true


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
    assert_equal(missing, set(), f"ways out missing from those offered ({offered})")

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
    assert_equal(set(get_args(Strategy.__value__)), set(OPTION_KEYS), "the ways out")


# The browser's tests read these cases too.
_SHARED = json.loads(Path(__file__).with_name("fit_cases.json").read_text())


def test_the_shared_cases_use_the_servers_rules():
    facts: AnalysisFacts = {
        "build": "a-build",
        "active_content_dropped": False,
        "plain_xmp_dropped": False,
        "signed": False,
        "pages": [],
        "fonts": [],
        "lines": [],
    }
    kept = KeptAnalysis(orjson.dumps(facts), b"[]", "its-digest")
    reply = orjson.loads(document_json("a-doc", expires_at=0.0, kept=kept, language="en"))
    assert_equal(_SHARED["rules"], reply["fit"], "the rules the shared cases are judged by")


@pytest.mark.parametrize("case", _SHARED["cases"], ids=lambda case: case["what"])
def test_a_line_is_too_long_only_past_its_room_as_the_browser_says(case):
    report = FitReport(
        delta_pt=case["delta_pt"], room_pt=case["room_pt"], original_pt=case["original_pt"]
    )
    options = options_for(case["delta_pt"], case["original_pt"], room_pt=case["room_pt"])

    percent = case["too_long_percent"]
    said = [] if percent is None else [Message("too_long", {"percent": percent})]
    assert_equal(report.describe(), said, "what's said of the length")
    assert_equal(report.ok, percent is None, "whether it fits")
    assert_equal([option.name for option in options], case["options"], "the ways out")


@pytest.mark.parametrize("case", _SHARED["line_cases"], ids=lambda case: case["what"])
def test_a_line_edited_as_one_is_fitted_by_the_runs_it_changes_as_the_browser_says(case):
    runs = [
        RunWidths(
            run["x"],
            original_pt=run["original_pt"],
            typed_pt=run["typed_pt"],
            changed=run["changed"],
        )
        for run in case["runs"]
    ]

    laid_out = runs_laid_out(
        runs,
        room_pt=case["room_pt"],
        strategy=case["strategy"],
        # .get: only a case of an edit ending at a split says so.
        at_split=case.get("at_split", False),
    )

    report = laid_out.report
    past = report.percent_past_room if report.too_long else None
    assert_equal(report.original_pt, case["original_pt"], "the changed runs' width")
    assert_equal(report.delta_pt, case["delta_pt"], "how much wider they're typed")
    assert_equal(past, case["too_long_percent"], "how far past its room, if it is")
    assert_equal([option.name for option in report.options], case["options"], "the ways out")
    for start, expected in zip(laid_out.starts, case["starts"], strict=True):
        assert_close(start, expected, 0.0001, "where a run starts")


_TURNED: QuarterTurn = 90


@pytest.mark.parametrize("case", _SHARED["verdicts"], ids=lambda case: case["what"])
def test_an_edits_verdict_is_the_one_the_browser_works_out_as_it_is_typed(case, tmp_path):
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

    approximate_why = report.why if report.state == "approximate" else None
    why = None if approximate_why is None else reason_of(approximate_why)
    judged = {"fidelity": report.state, "why": why}
    assert_equal(
        judged, case["span"], "the span, judged on its own words, as the browser gets it"
    )
    assert_equal(report.in_file, case["stored"], "whether the file's own copy can draw it")
    assert_equal(verdict.fidelity, case["fidelity"], "the edit's verdict")
