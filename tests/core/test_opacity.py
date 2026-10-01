"""A redraw paints as see-through as the text it replaces."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import LineToDraw, open_pdf
from tests.conftest import each_span
from tests.core.conftest import TRANSLUCENT
from tests.helpers import assert_equal

_BYTE_MAX = 255  # MuPDF reads opacity back as 0 to 255


@pytest.mark.parametrize(
    ("fixture", "text", "substitute"),
    [
        ("translucent", "Valid in March", False),  # its own font, by letter
        ("translucent", "Válid until March", True),  # no á in the trimmed font
        ("coded_translucent", "BA AB", False),  # its own font, by code
    ],
    ids=["own font", "substitute", "by code"],
)
def test_a_redraw_keeps_the_originals_opacity(request, tmp_path, fixture, text, substitute):
    """Grey small print at 60% came back solid black."""
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(request.getfixturevalue(fixture)) as engine:
        [span] = engine.index()
        missing = engine.missing(span, text)
        engine.remove([span], then_drawn=[LineToDraw(span, text)])
        engine.draw(span, text)
        engine.save(out)

    assert_equal(bool(missing), substitute, f"drawn in the substitute (it lacks {missing})")
    [drawn] = list(each_span(pymupdf.open(out)[0].get_text("dict")["blocks"]))
    assert_equal(drawn["text"], text, "what redrew")
    assert_equal(drawn["alpha"], round(TRANSLUCENT * _BYTE_MAX), "its opacity, of 255")
