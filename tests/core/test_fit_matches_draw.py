"""The face the fit names is the face the saved file draws the line in (Rule 1).

The fit and the draw once worked the face out apart: text typed with a
separate accent, as macOS pastes it, got "drawn in Noto Sans" from the fit and
Carlito in the file.
"""

from __future__ import annotations

import unicodedata

import pymupdf
import pytest

from squidpdf.core import Engine, LineToDraw, Span, index_of, new_text, open_pdf
from tests.conftest import each_span, named_only, saved_as
from tests.helpers import assert_equal

# The own font: MuPDF's Nimbus Sans, stored trimmed and renamed Calibri, so its
# look-alike is Carlito, which has é whole but no separate accent to put on an e.
_OWN_TEXT = "Café Bistro"
_OWN_READS_AS = "NimbusSans-Regular"  # the stored font's own name, which a redraw in it reads
_CALIBRI = "ABCDEF+Calibri"
_CARLITO = "Carlito Regular"
_SIZE = 12.0
_INSERT_AT = (72.0, 300.0)


def _own_font_file(path: str) -> str:
    """One line in a stored, trimmed Nimbus Sans the file calls Calibri: C a f é and Bistro."""
    doc = pymupdf.open()
    page = doc.new_page()
    # "own" is only the name the page files the font under.
    page.insert_font(fontname="own", fontbuffer=pymupdf.Font("helv").buffer)
    page.insert_text((72, 96), _OWN_TEXT, fontname="own", fontsize=_SIZE)
    doc.subset_fonts(verbose=False)
    [(xref, *_)] = page.get_fonts()
    # A two-byte font names itself twice: on the font and on the one inside it.
    _value_type, descendants = doc.xref_get_key(xref, "DescendantFonts")
    for font_xref in (xref, int(descendants.strip("[]").split()[0])):
        doc.xref_set_key(font_xref, "BaseFont", f"/{_CALIBRI}")
    doc.save(path)
    return path


def _font_drawing(path: str, text: str) -> str:
    """The font the saved file draws `text` in, read back from its first page."""
    wanted = unicodedata.normalize("NFC", text)
    [font] = {
        piece["font"]
        for piece in each_span(pymupdf.open(path)[0].get_text("dict")["blocks"])
        if wanted in unicodedata.normalize("NFC", piece["text"])
    }
    return font


def _named_by_fit(engine: Engine, span: Span, text: str) -> str | None:
    """The face the fit names for `text` here; None when it says the own font draws it.

    It names one when the own font can't be used at all, or when the line
    switches: the own font lacks a letter that a face we ship has.
    """
    [report] = engine.assess(index_of([span]))
    plan = engine.fit.plan_for(span, text)
    switches = [ch for ch in plan.missing if ch not in plan.left_out]
    named = switches or not report.in_file
    return engine.fit.substitute(span, text, plan=plan) if named else None


def _redrawn(path: str, out: str, *, text: str, insert: bool) -> str | None:
    """Redraw the file's first line as `text`, or add it as new text in Calibri, and save.

    Returns the face the fit named first, as the fit is asked before the edit.
    """
    with open_pdf(path) as engine:
        span = next(iter(engine.index()))
        if insert:
            span = new_text(0, origin=_INSERT_AT, text=text, size=_SIZE, font="Calibri")
        named = _named_by_fit(engine, span, text)
        if not insert:
            engine.remove([span], then_drawn=[LineToDraw(span, text)])
        engine.draw(span, text)
        engine.save(out)
    return named


@pytest.mark.parametrize(
    "spelling", ["NFC", "NFD"], ids=["typed whole", "typed with a separate accent"]
)
@pytest.mark.parametrize(
    ("fixture", "text", "insert", "face"),
    [
        ("own", "Café", False, None),
        ("own", "Qcafé", False, _CARLITO),
        ("named", "Café", False, _CARLITO),
        ("named", "Café", True, _CARLITO),
    ],
    ids=[
        "its own font draws it",
        "its own font lacks a letter",
        "its font is only named",
        "an insert in a font the page can't lend",
    ],
)
def test_the_face_the_fit_names_is_the_one_the_saved_file_draws(
    tmp_path, spelling, fixture, text, insert, face
):
    path = str(tmp_path / "original.pdf")
    if fixture == "own":
        _own_font_file(path)
    else:
        named_only(path, "Calibri")
    typed = unicodedata.normalize(spelling, text)
    out = str(tmp_path / "saved.pdf")

    named = _redrawn(path, out, text=typed, insert=insert)

    drawn = _font_drawing(out, typed)
    expected = (face, _OWN_READS_AS if face is None else saved_as(face))
    assert_equal((named, drawn), expected, "the face the fit named, and the font saved")
