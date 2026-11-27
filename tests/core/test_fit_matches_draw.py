from __future__ import annotations

import unicodedata

import pymupdf
import pytest

from squidpdf.core import Engine, LineToDraw, Span, index_of, new_text, open_pdf
from tests.conftest import each_span, name_two_byte_font, named_only, saved_as
from tests.helpers import assert_equal

_OWN_TEXT = "Café Bistro"
_OWN_READS_AS = "NimbusSans-Regular"
_CALIBRI = "Calibri"
_CARLITO = "Carlito Regular"
_SIZE = 12.0
_INSERT_AT = (72.0, 300.0)


def _own_font_file(path: str) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="own", fontbuffer=pymupdf.Font("helv").buffer)
    page.insert_text((72, 96), _OWN_TEXT, fontname="own", fontsize=_SIZE)
    doc.subset_fonts(verbose=False)
    [(font_object, *_)] = page.get_fonts()
    name_two_byte_font(doc, font_object, _CALIBRI)
    doc.save(path)
    return path


def _font_drawing(path: str, text: str) -> str:
    wanted = unicodedata.normalize("NFC", text)
    [font] = {
        piece["font"]
        for piece in each_span(pymupdf.open(path)[0].get_text("dict")["blocks"])
        if wanted in unicodedata.normalize("NFC", piece["text"])
    }
    return font


def _named_by_fit(engine: Engine, span: Span, text: str) -> str | None:
    [report] = engine.assess(index_of([span]))
    plan = engine.fit.plan_for(span, text)
    switches = [ch for ch in plan.missing if ch not in plan.left_out]
    named = switches or not report.in_file
    return engine.fit.substitute(span, text, plan=plan) if named else None


def _redrawn(path: str, out: str, *, text: str, insert: bool) -> str | None:
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
        ("named", "Café\u2019s \u201cmenu\u201d \u2013 €5", False, _CARLITO),
    ],
    ids=[
        "its own font draws it",
        "its own font lacks a letter",
        "its font is only named",
        "an insert in a font the page can't lend",
        "letters past Latin-1, as macOS types them",
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
