"""Turning edits into real changes: replace, redact, and verify."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.editing import Redact, Replace, apply, verify_redactions
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true


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
    page2 = [s for s in index if s.page == 1]
    assert_true(len(page2) >= 2, f"spans in one font on page 2, found {len(page2)}")

    apply(engine, [Replace(s.id, s.text) for s in page2], index)
    engine.save(str(tmp_path / "redrawn.pdf"))

    fonts = pymupdf.open(tmp_path / "redrawn.pdf")[1].get_fonts()
    assert_equal(len(fonts), 1, f"fonts on a page with {len(page2)} spans redrawn")
    _xref, ext, _type, basefont, *_ = fonts[0]
    assert_equal(basefont, page2[0].font, "the font the redraw used")
    assert_true(ext != "n/a", f"the redraw's font is embedded, got {fonts[0]}")


def test_unknown_span_is_an_error(engine):
    with pytest.raises(KeyError):
        apply(engine, [Replace("nosuchid", "x")], engine.index())
