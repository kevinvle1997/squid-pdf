"""Engine behaviour, against a generated fixture.

These cover the shape. They do not answer whether redraw quality is convincing —
only real documents do that, which is what the corpus is for.
"""

from __future__ import annotations

from collections.abc import Callable

import pymupdf
import pytest

from helpers import assert_all, assert_any
from squidpdf.core import Fidelity, FidelityReport, MuPDFEngine, Span, green_rate
from squidpdf.core.coverage import Coverage
from squidpdf.editing import Redact, Replace, apply, check, verify_redactions


@pytest.fixture(scope="module")
def pdf(tmp_path_factory) -> str:
    """Page 1 references its fonts; page 2 embeds one and subsets it."""
    path = tmp_path_factory.mktemp("fx") / "sample.pdf"
    doc = pymupdf.open()

    p1 = doc.new_page()
    p1.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    p1.insert_text(
        (72, 128),
        "Made on 14 March 2026 between Wescott and Rowe.",
        fontname="tiro",
        fontsize=11,
    )

    p2 = doc.new_page()
    p2.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    p2.insert_text(
        (72, 96),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def engine(pdf):
    with MuPDFEngine(pdf) as eng:
        yield eng


def test_index_finds_text(engine):
    index = engine.index()
    assert len(index) >= 3
    assert_any(index, lambda s: "14 March 2026" in s.text, describe=lambda s: repr(s.text))


def test_span_ids_are_stable_across_reindexing(pdf):
    """The client holds these. They must not move."""
    with MuPDFEngine(pdf) as a, MuPDFEngine(pdf) as b:
        assert [s.id for s in a.index()] == [s.id for s in b.index()]


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
    assert 0.0 < rate < 1.0  # this fixture is deliberately mixed


def test_subsetted_font_reports_emptied_glyphs_as_missing(engine):
    """A subset keeps the charset but empties unused outlines.

    pymupdf's has_glyph returns an id for those, which is how this came to be
    reported as available. Coverage asks the glyph to draw instead.
    """
    span = next(s for s in engine.index() if s.page == 1)
    assert "é" in engine.missing(span, "Février")
    assert engine.missing(span, "March") == []


def test_coverage_treats_whitespace_as_drawable():
    cov = Coverage(b"")  # unparseable
    assert cov.covers(" ") is True
    assert cov.covers("x") is True  # never claims a problem it cannot prove


def test_check_reports_overflow_with_options(engine):
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    longer = span.text + " and much more time and more months and more"
    assert engine.missing(span, longer) == []  # isolate the width case
    fit = check(engine, span, longer)
    assert not fit.ok
    assert "too long" in fit.describe()
    assert {o.name for o in fit.options} >= {"shrink", "as-is"}


def test_check_is_quiet_when_nothing_is_wrong(engine):
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    fit = check(engine, span, "Delivery begins 2 March 2026")
    assert fit.ok
    assert fit.describe() is None
    assert fit.options == []


def test_replace_swaps_the_text(engine, tmp_path):
    index = engine.index()
    span = next(s for s in index if "14 March 2026" in s.text)
    out = tmp_path / "edited.pdf"

    apply(engine, [Replace(span.id, "Delivery begins 2 April 2026")], index)
    engine.save(str(out))

    edited = pymupdf.open(out)[span.page].get_text()
    assert "2 April 2026" in edited
    assert "14 March 2026" not in edited


def test_redaction_really_removes_the_text(engine, tmp_path):
    """A covering rectangle would pass a visual check and fail this."""
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    edits = [Redact(span.id)]

    apply(engine, edits, index)
    engine.save(str(tmp_path / "redacted.pdf"))

    assert verify_redactions(engine, edits, index)[span.id] is True
    text = "".join(p.get_text() for p in pymupdf.open(tmp_path / "redacted.pdf"))
    assert span.text not in text


def test_unknown_span_is_an_error(engine):
    with pytest.raises(KeyError):
        apply(engine, [Replace("nosuchid", "x")], engine.index())
