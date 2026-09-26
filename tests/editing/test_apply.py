"""Turning edits into real changes: replace, redact, and verify."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import MuPDFEngine, Span, words
from squidpdf.core.fonts import base14_for
from squidpdf.editing import (
    BadReference,
    Notice,
    Redact,
    Replace,
    Skipped,
    apply,
    verify_redactions,
)
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true

_LONGER = "!!"  # a few points past the original: within reach of shrink and condense
_FAR_LONGER = " and Co. Ltd"  # a fifth past it: too far to condense
_EDGE_PT = 0.5  # how far past the original's end a fitted run may land, in points
_HEIGHT_PT = 0.1  # finer than the shrink changes a line's height, coarser than rounding


def _drawn(path, page: int, needle: str) -> dict:
    """The span on a saved page whose text contains `needle`, as MuPDF reads it back."""
    blocks = pymupdf.open(path)[page].get_text("dict")["blocks"]
    # .get: an image block has no lines.
    spans = [
        span for block in blocks for line in block.get("lines", []) for span in line["spans"]
    ]
    for span in spans:
        if needle in span["text"]:
            return span
    raise LookupError(f"nothing drawn on page {page} contains {needle!r}")


def _substituted(engine) -> Span:
    """A span in a font the file only references, drawn by its base-14 stand-in."""
    return next(
        s for s in engine.index() if s.page == REFERENCED_PAGE and s.text.startswith("Made")
    )


def test_replace_swaps_the_text(engine, tmp_path):
    index = engine.index()
    span = next(s for s in index if "14 March 2026" in s.text)
    out = tmp_path / "edited.pdf"

    apply(engine, [Replace(span.id, "Delivery begins 2 April 2026")], index)
    engine.save(str(out))

    edited = pymupdf.open(out)[span.page].get_text()
    assert_in("2 April 2026", edited, "the saved page after a replace")
    assert_not_in("14 March 2026", edited, "the saved page after a replace")


def test_redaction_really_removes_the_text(engine, tmp_path):
    """A covering rectangle would pass a visual check and fail this."""
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    edits = [Redact(span.id)]

    apply(engine, edits, index)
    engine.save(str(tmp_path / "redacted.pdf"))

    verified = verify_redactions(engine, edits, index)[span.id]
    assert_true(verified is True, "verify_redactions() result for the redacted span")
    text = "".join(p.get_text() for p in pymupdf.open(tmp_path / "redacted.pdf").pages())
    assert_not_in(span.text, text, "the saved page after a redact")


def test_redraws_in_one_font_embed_it_once_per_page(engine, tmp_path):
    """A resource per redrawn span piled up on the page."""
    index = engine.index()
    embedded = [s for s in index if s.page == EMBEDDED_PAGE]
    assert_true(len(embedded) >= 2, f"spans in one font on page 2, found {len(embedded)}")

    apply(engine, [Replace(s.id, s.text) for s in embedded], index)
    engine.save(str(tmp_path / "redrawn.pdf"))

    fonts = pymupdf.open(tmp_path / "redrawn.pdf")[EMBEDDED_PAGE].get_fonts()
    assert_equal(len(fonts), 1, f"fonts on a page with {len(embedded)} spans redrawn")
    _xref, ext, _type, basefont, *_ = fonts[0]
    assert_equal(basefont, embedded[0].font, "the font the redraw used")
    assert_true(ext != "n/a", f"the redraw's font is embedded, got {fonts[0]}")


def test_an_underline_under_a_replaced_span_survives(tmp_path):
    path, out = tmp_path / "underlined.pdf", tmp_path / "edited.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Total due: 48,500", fontname="tiro", fontsize=11)
    page.draw_line((73, 101.5), (148, 101.5))  # just under the baseline, inside the text's box
    doc.save(path)

    with MuPDFEngine(str(path)) as eng:
        index = eng.index()
        apply(eng, [Replace(next(iter(index)).id, "Total due: 49,500")], index)
        eng.save(str(out))

    edited = pymupdf.open(out)[0]
    assert_equal(len(edited.get_drawings()), 1, "lines left under the replaced text")
    assert_not_in("48,500", edited.get_text(), "the saved page after a replace")


def test_a_character_the_font_lacks_draws_the_whole_run_in_the_substitute(engine, tmp_path):
    """The subset has no é. Drawn in it, the letter would be blank.

    The substitute has no € either, so that is left out, and the user is told.
    """
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and "14 March" in s.text)
    out = tmp_path / "accented.pdf"

    applied = apply(engine, [Replace(span.id, "Delivery begins 14 Février 2026€")], index)
    engine.save(str(out))

    substitute = pymupdf.Font(base14_for(span.font)).name
    drawn = _drawn(out, EMBEDDED_PAGE, "Février")
    assert_equal(drawn["font"], substitute, "the font that drew the run")
    left_out = Notice(span.id, words.LEFT_OUT.format(letters="€"))
    assert_equal(applied.notices, [left_out], "what render tells the user")


@pytest.mark.parametrize("strategy", ["shrink", "condense"])
def test_a_fitting_strategy_ends_the_run_where_the_original_did(engine, tmp_path, strategy):
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / f"{strategy}.pdf"

    apply(engine, [Replace(span.id, span.text + _LONGER, strategy)], index)
    engine.save(str(out))

    drawn = _drawn(out, REFERENCED_PAGE, _LONGER)
    _x0, top, end, bottom = drawn["bbox"]
    assert_true(end <= span.bbox.x1 + _EDGE_PT, f"{strategy} ends at {end}, not {span.bbox.x1}")
    # Height, not size: MuPDF reports a narrowed run's size as smaller too.
    height = bottom - top
    shorter = height < span.bbox.height - _HEIGHT_PT
    assert_equal(shorter, strategy == "shrink", f"a run {height} high is shorter")


def test_a_strategy_not_offered_is_drawn_as_is(engine, tmp_path):
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "long.pdf"

    apply(engine, [Replace(span.id, span.text + _FAR_LONGER, "condense")], index)
    engine.save(str(out))

    drawn = _drawn(out, REFERENCED_PAGE, _FAR_LONGER)
    _x0, _y0, end, _y1 = drawn["bbox"]
    assert_equal(drawn["size"], span.size, "type size of a run left long")
    assert_true(end > span.bbox.x1 + _EDGE_PT, "a run left long runs long")


def test_an_edit_pointing_at_nothing_is_skipped_and_the_rest_drawn(engine, tmp_path):
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "skipped.pdf"

    edits = [Replace("nosuchid", "x"), Replace(span.id, "Made on 2 April 2026.")]
    skipped = apply(engine, edits, index).skipped
    engine.save(str(out))

    assert_equal(skipped, [Skipped(0, "bad_reference", words.NO_SPAN)], "skipped")
    edited = pymupdf.open(out)[REFERENCED_PAGE].get_text()
    assert_in("2 April 2026", edited, "the edit that was good")


def test_a_redaction_pointing_at_nothing_is_an_error(engine):
    """Skipping it would leave the text the user asked to remove."""
    with pytest.raises(BadReference):
        apply(engine, [Redact("nosuchid")], engine.index())
