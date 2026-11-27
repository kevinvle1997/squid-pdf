from __future__ import annotations

import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, assert_never, cast

import pymupdf
import pytest
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import (
    DEFAULT_DRIVER_FACTORY,
    FACES,
    Message,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    new_text,
    open_pdf,
    strip_subset,
    words,
)
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.editing import (
    Applied,
    DeletePage,
    Edit,
    Insert,
    Notice,
    Redact,
    RedactionFailed,
    Replace,
    ReplaceRuns,
    RotatePage,
    RunText,
    apply_edits,
    insert_fit,
    redactions_of,
    replace_fit,
    resolve,
    save_edited,
)
from squidpdf.editing.apply import (
    Resolved,
    edited_by,
    page_order,
    planned_of,
    redacted_in,
)
from squidpdf.editing.types import FileNotice, InsertNotice, SpanNotice
from tests.conftest import (
    EMBEDDED_PAGE,
    REFERENCED_PAGE,
    SENTENCE,
    cannot_cut,
    each_span,
    named_only,
    saved_as,
    write_runs,
)
from tests.helpers import (
    assert_at_least,
    assert_at_most,
    assert_close,
    assert_equal,
    assert_in,
    assert_not_in,
    assert_true,
)

_LONGER = "!!"
_FAR_LONGER = " and Co. Ltd"
_EDGE_PT = 0.5
_HEIGHT_PT = 0.1  # finer than the shrink changes a line's height, coarser than rounding
_SAME_WIDTH_PT = 0.25
_SAME_START_PT = 0.25
_ONE_EDIT_ADDS_AT_MOST = 20_000
_HINTING = ("fpgm", "prep", "cvt ")


def _resolved(engine, index: SpanIndex, edits: Sequence[Edit]) -> Resolved:
    return resolve(engine, index, free_space=engine.free_space(index), edits=edits)


def _apply(engine, edits: Sequence[Edit], index: SpanIndex) -> Applied:
    resolved = _resolved(engine, index, edits)
    return apply_edits(engine, resolved, planned_of(engine, resolved))


def _drawn(path, page: int, needle: str) -> dict:
    blocks = pymupdf.open(path)[page].get_text("dict")["blocks"]
    for span in each_span(blocks):
        if needle in span["text"]:
            return span
    raise LookupError(f"nothing drawn on page {page} contains {needle!r}")


def _said(notices: list[Notice]) -> list[tuple[str | int | None, str]]:
    return [(_about(notice), words.render(notice.detail)) for notice in notices]


def _about(notice: Notice) -> str | int | None:
    if isinstance(notice, SpanNotice):
        return notice.span_id
    if isinstance(notice, InsertNotice):
        return notice.position
    if isinstance(notice, FileNotice):
        return None
    assert_never(notice)


def _stored_file(path: str, page: int, font: str) -> bytes:
    doc = pymupdf.open(path)
    [font_object] = [
        font_object
        for font_object, _ext, _kind, name, *_ in doc[page].get_fonts()
        if name == font
    ]
    _name, _ext, _kind, buffer = doc.extract_font(font_object)
    return buffer


def _assert_cut(path, page: int, face: str, text: str) -> None:
    cut = _stored_file(str(path), page, face)
    shipped = face_bytes(FACES[face])
    assert_at_most(len(cut), len(shipped) - 1, f"bytes {face} stored, cut from {len(shipped)}")
    assert_equal(coverage_of(cut).missing(text), [], f"letters {face} lost in the cut")
    kept = [table for table in _HINTING if table in TTFont(io.BytesIO(cut))]
    shipped_hinting = [table for table in _HINTING if table in TTFont(io.BytesIO(shipped))]
    assert_equal(kept, shipped_hinting, f"{face}'s hinting, cut and as shipped")


def _squashed(font: str) -> str:
    return "".join(ch for ch in strip_subset(font).lower() if ch.isalnum())


def _insert_as_span(insert: Insert) -> Span:
    return new_text(
        insert.page,
        origin=insert.origin,
        text=insert.text,
        size=insert.size,
        font=insert.font,
        color=insert.color,
    )


def _substituted(engine) -> Span:
    return next(
        s for s in engine.index() if s.page == REFERENCED_PAGE and s.text.startswith("Made")
    )


def test_redaction_really_removes_the_text(engine, tmp_path):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE)
    edits = [Redact(span.id)]
    resolved = _resolved(engine, index, edits)
    planned = planned_of(engine, resolved)
    edited = edited_by(planned.steps)
    redactions = redactions_of(engine, resolved, redacted_in(resolved), edited=edited)

    apply_edits(engine, resolved, planned)
    engine.save(str(tmp_path / "redacted.pdf"))

    drawn = redactions.drawn(engine)
    assert_true(drawn[span.id].verified, "the in-memory verdict on the redacted span")
    saved = pymupdf.open(tmp_path / "redacted.pdf")
    text = "".join(page.get_text() for page in saved.pages())
    assert_not_in(span.text, text, "the saved page after a redact")
    box = pymupdf.Rect(span.bbox.x0, span.bbox.y0, span.bbox.x1, span.bbox.y1)
    left = "".join(saved[span.page].get_textbox(box).split())
    assert_equal(left, "", "letters left in the redacted span's box")


def test_text_under_a_black_box_is_not_gone(pdf, tmp_path):
    with open_pdf(pdf) as engine:
        span = next(iter(engine.index()))
    covered = tmp_path / "covered.pdf"
    doc = pymupdf.open(pdf)
    box = span.bbox
    doc[span.page].draw_rect(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1), fill=(0, 0, 0))
    doc.save(covered)

    with open_pdf(str(covered)) as engine:
        assert_equal(
            engine.redaction.still_there([span]), [span], "text under a black box, still there"
        )
        engine.remove([span], then_drawn=[])
        assert_equal(
            engine.redaction.still_there([span]),
            [],
            "the same text really removed, still there",
        )


def test_redraws_in_one_font_embed_it_once_per_page(engine, tmp_path):
    index = engine.index()
    embedded = [s for s in index if s.page == EMBEDDED_PAGE]
    assert_at_least(len(embedded), 2, "spans in one font on page 2")

    _apply(engine, [Replace(s.id, s.text) for s in embedded], index)
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
    page.draw_line((73, 101.5), (148, 101.5))
    doc.save(path)

    with open_pdf(str(path)) as engine:
        index = engine.index()
        _apply(engine, [Replace(next(iter(index)).id, "Total due: 49,500")], index)
        engine.save(str(out))

    edited_page = pymupdf.open(out)[0]
    assert_equal(len(edited_page.get_drawings()), 1, "lines left under the replaced text")
    assert_not_in("48,500", edited_page.get_text(), "the saved page after a replace")
    assert_in("49,500", edited_page.get_text(), "the saved page after a replace")


def test_a_character_the_font_lacks_draws_the_whole_run_in_the_substitute(
    engine, pdf, tmp_path
):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and "14 March" in s.text)
    out, unedited = tmp_path / "accented.pdf", tmp_path / "unedited.pdf"

    applied = _apply(engine, [Replace(span.id, "Delivery begins 14 Février 2026中")], index)
    said_on_save = engine.save(str(out))
    with open_pdf(pdf) as plain:
        plain.save(str(unedited))

    drawn = _drawn(out, EMBEDDED_PAGE, "Février")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    left_out = (span.id, words.sentence("left_out").format(letters="中"))
    assert_equal(_said(applied.notices), [left_out], "what render tells the user")
    assert_equal(said_on_save, [], "what save tells the user")
    _assert_cut(
        out, EMBEDDED_PAGE, "Liberation Serif Regular", "Delivery begins 14 Février 2026"
    )
    added = out.stat().st_size - unedited.stat().st_size
    assert_at_most(added, _ONE_EDIT_ADDS_AT_MOST, "bytes the edit added to the file")
    own = strip_subset(span.font)
    [own_name] = [f[3] for f in pymupdf.open(pdf)[EMBEDDED_PAGE].get_fonts() if own in f[3]]
    assert_equal(
        _stored_file(str(out), EMBEDDED_PAGE, own_name),
        _stored_file(pdf, EMBEDDED_PAGE, own_name),
        "the document's own font file, saved and as it came",
    )


def test_a_look_alike_with_the_same_widths_moves_nothing(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(Subsetter, "subset", cannot_cut)
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "same.pdf"

    _apply(engine, [Replace(span.id, span.text)], index)
    said_on_save = engine.save(str(out))

    said = words.sentence("face_not_trimmed").format(font="Liberation Serif Regular")
    assert_equal([words.render(m) for m in said_on_save], [said], "what save tells the user")
    stored = _stored_file(str(out), REFERENCED_PAGE, "Liberation Serif Regular")
    shipped = face_bytes(FACES["Liberation Serif Regular"])
    assert_true(stored == shipped, "the whole shipped file went in")
    drawn = _drawn(out, REFERENCED_PAGE, "Made on")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    x0, _y0, x1, _y1 = drawn["bbox"]
    moved = abs(x0 - span.bbox.x0) + abs(x1 - span.bbox.x1)
    assert_at_most(moved, _SAME_WIDTH_PT, "points the line's ends moved")


def test_letters_the_look_alike_lacks_draw_the_whole_line_in_the_broadest_face(tmp_path):
    path = named_only(str(tmp_path / "cambria.pdf"), "Cambria")
    out = str(tmp_path / "greek.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        span = next(iter(index))
        fit = replace_fit(engine, span, "Hi Ωμέγα", room_pt=0.0).report
        applied = _apply(engine, [Replace(span.id, "Hi Ωμέγα")], index)
        engine.save(out)

    greek = "Ω or μ or έ or γ or α"  # noqa: RUF001 (Greek on purpose: Caladea has none)
    said = words.sentence("missing").format(chars=greek, font="Noto Serif Regular")
    assert_equal(words.render_all(fit.describe()), said, "what the fit says")
    assert_equal(applied.notices, [], "nothing left out")
    drawn = _drawn(out, 0, "Hi")
    expected = ("Hi Ωμέγα", saved_as("Noto Serif Regular"))
    assert_equal((drawn["text"], drawn["font"]), expected, "what drew, and in what")
    _assert_cut(out, 0, "Noto Serif Regular", "Hi Ωμέγα")


def test_new_text_in_a_pages_font_as_its_text_names_it_is_drawn_in_that_font(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="own", fontbuffer=face_bytes(FACES["Liberation Serif Regular"]))
    page.insert_text((72, 96), "Terms of payment", fontname="own", fontsize=12)
    path, out = tmp_path / "own.pdf", tmp_path / "inserted.pdf"
    doc.save(path)
    with open_pdf(str(path)) as engine:
        [span] = engine.index()
        insert = Insert(0, (72.0, 300.0), "Signed", 12.0, span.font)
        fit = insert_fit(engine, insert).report
        _apply(engine, [insert], engine.index())
        engine.save(str(out))

    assert_equal((span.font, fit.unavailable), ("LiberationSerif", ""), "the font, and usable")
    drawn = _drawn(out, 0, "Signed")["font"]
    assert_equal(drawn, _drawn(out, 0, "Terms")["font"], "what drew it: the page's own font")


@pytest.mark.parametrize(
    ("font", "text", "face", "said"),
    [
        ("Caveat Bold", "Signed", "Caveat Bold", None),
        (
            "Caveat Bold",
            "Signed Ω",
            "Noto Sans Bold",
            words.sentence("missing").format(chars="Ω", font="Noto Sans Bold"),
        ),
        (
            "Comic Sans",
            "Signed",
            "Liberation Sans Regular",
            words.sentence("chosen_unavailable").format(
                chosen="Comic Sans", font="Liberation Sans Regular"
            ),
        ),
    ],
)
def test_new_text_is_drawn_in_the_face_its_fit_names(engine, tmp_path, font, text, face, said):
    out = tmp_path / "inserted.pdf"
    insert = Insert(REFERENCED_PAGE, (72.0, 700.0), text, 12.0, font)

    fit = insert_fit(engine, insert).report
    _apply(engine, [insert], engine.index())
    engine.save(str(out))

    assert_equal(words.render_all(fit.describe()), said, "what the fit says")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    assert_equal(
        (drawn["text"], drawn["font"]), (text, saved_as(face)), "what drew, and in what"
    )
    _assert_cut(out, REFERENCED_PAGE, face, text)


def test_a_space_the_face_lacks_sends_the_line_to_one_that_has_it(engine, tmp_path):
    out = tmp_path / "spaced.pdf"
    text = "15\u202f000 EUR"
    insert = Insert(REFERENCED_PAGE, (72.0, 700.0), text, 20.0, "Liberation Mono Regular")

    fit = insert_fit(engine, insert).report
    measured = engine.fit.measure(_insert_as_span(insert), text)
    _apply(engine, [insert], engine.index())
    engine.save(str(out))

    assert_equal(fit.missing, ["\u202f"], "what the fit says the face lacks")
    said = (
        "no narrow no-break space in this font, so the line is drawn in our Noto Sans Regular"
    )
    assert_equal(words.render_all(fit.describe()), said, "what the user reads of it")
    drawn = _drawn(out, REFERENCED_PAGE, "15")
    expected = (text, saved_as("Noto Sans Regular"))
    assert_equal((drawn["text"], drawn["font"]), expected, "what drew, and in what")
    x0, _y0, x1, _y1 = drawn["bbox"]
    width = x1 - x0
    assert_close(width, measured, _SAME_WIDTH_PT, "the drawn width, against the measured")


@pytest.mark.parametrize("strategy", ["shrink", "condense"])
def test_a_fitting_strategy_ends_the_run_where_the_original_did(
    engine, pdf, tmp_path, strategy
):
    index = engine.index()
    span = _substituted(engine)
    out, as_is = tmp_path / f"{strategy}.pdf", tmp_path / "as-is.pdf"

    _apply(engine, [Replace(span.id, span.text + _LONGER, strategy)], index)
    engine.save(str(out))
    with open_pdf(pdf) as plain:
        _apply(plain, [Replace(span.id, span.text + _LONGER)], plain.index())
        plain.save(str(as_is))

    drawn = _drawn(out, REFERENCED_PAGE, _LONGER)
    _x0, top, end, bottom = drawn["bbox"]
    assert_at_most(end, span.bbox.x1 + _EDGE_PT, f"where {strategy} ends")
    # Height, not size: MuPDF reports a narrowed run's size as smaller too.
    height = bottom - top
    _x0, as_is_top, _x1, as_is_bottom = _drawn(as_is, REFERENCED_PAGE, _LONGER)["bbox"]
    shorter = height < as_is_bottom - as_is_top - _HEIGHT_PT
    assert_equal(shorter, strategy == "shrink", f"a run {height} high is shorter")


def test_a_strategy_not_offered_is_drawn_as_is(engine, tmp_path):
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "long.pdf"

    _apply(engine, [Replace(span.id, span.text + _FAR_LONGER, "condense")], index)
    engine.save(str(out))

    drawn = _drawn(out, REFERENCED_PAGE, _FAR_LONGER)
    _x0, _y0, end, _y1 = drawn["bbox"]
    assert_equal(drawn["size"], span.size, "type size of a run left long")
    assert_at_least(end, span.bbox.x1 + _EDGE_PT, "where a run left long ends")


def test_new_text_with_a_letter_no_font_has_is_drawn_without_it_as_its_fit_said(
    engine, tmp_path
):
    out = tmp_path / "signed.pdf"
    signed = Insert(REFERENCED_PAGE, (72.0, 700.0), "Signed: Zoë 中", 12.0, "Caveat Regular")

    fit = insert_fit(engine, signed).report
    applied = _apply(engine, [signed], engine.index())
    engine.save(str(out))

    left_out = words.sentence("left_out").format(letters="中")
    assert_equal(_said(applied.notices), [(0, left_out)], "what render says")
    assert_equal(fit.left_out, ["中"], "what the fit said would be left out")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    expected = ("Signed: Zoë", saved_as("Caveat Regular"))
    assert_equal((drawn["text"].strip(), drawn["font"]), expected, "drawn")


def _three_lines(path: str, *, spacing: float, font: str) -> str:
    """At 1.15 to 1.2 times their size apart, as Word sets them, letters reach the next line."""
    doc = pymupdf.open()
    page = doc.new_page()
    for number, line in enumerate(_LINES):
        baseline = 100 + number * _LINE_SIZE * spacing
        page.insert_text((72, baseline), line, fontname=font, fontsize=_LINE_SIZE)
    doc.save(path)
    return path


_LINE_SIZE = 12
_LINES = ("Line above the edited one", "Total due: 48,500 now", "Next line of the contract")


@pytest.mark.parametrize("edit", ["replace", "redact"])
@pytest.mark.parametrize("font", ["tiro", "helv"])
@pytest.mark.parametrize("spacing", [1.0, 1.15, 1.2])
def test_an_edit_leaves_the_lines_above_and_below_alone(tmp_path, edit, font, spacing):
    path = _three_lines(str(tmp_path / "lines.pdf"), spacing=spacing, font=font)
    out = str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [middle] = [span for span in index if span.text == _LINES[1]]
        change: Edit = (
            Replace(middle.id, "Total due: 49,500 now")
            if edit == "replace"
            else Redact(middle.id)
        )
        _apply(engine, [change], index)
        engine.save(out)

    left = pymupdf.open(out)[0].get_text()
    assert_in(_LINES[0], left, "the line above")
    assert_in(_LINES[2], left, "the line below")
    assert_not_in("48,500", left, "the edited line's old text")


def test_an_edit_leaves_the_text_under_the_files_own_redaction_mark(tmp_path):
    doc = pymupdf.open(_three_lines(str(tmp_path / "lines.pdf"), spacing=1.2, font="helv"))
    page = doc[0]
    page.add_redact_annot(page.search_for(_LINES[2])[0], fill=(0, 0, 0))
    path = str(tmp_path / "marked.pdf")
    doc.save(path)
    out = str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [first] = [span for span in index if span.text == _LINES[0]]
        _apply(engine, [Replace(first.id, "Line above the changed one")], index)
        engine.save(out)

    saved = pymupdf.open(out)[0]
    left = saved.get_text()
    assert_in(_LINES[1], left, "the line between")
    assert_in(_LINES[2], left, "the line under the file's own mark")
    assert_equal(
        [annotation.type[1] for annotation in saved.annots()],
        ["Redact"],
        "the file's own mark, still there and still not applied",
    )


def test_an_edit_leaves_a_touching_word_in_another_font_alone(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Total:", fontname="helv", fontsize=_LINE_SIZE)
    end = 72 + pymupdf.get_text_length("Total:", fontname="helv", fontsize=_LINE_SIZE)
    page.insert_text((end - 0.5, 100), "Jones", fontname="tiro", fontsize=_LINE_SIZE)
    path = str(tmp_path / "touching.pdf")
    doc.save(path)
    out = str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [name] = [span for span in index if span.text == "Jones"]
        _apply(engine, [Redact(name.id)], index)
        engine.save(out)

    left = pymupdf.open(out)[0].get_text()
    assert_in("Total:", left, "the word before")
    assert_not_in("Jones", left, "the redacted word")


def test_an_edit_to_turned_text_leaves_the_lines_beside_it_alone(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    for number, line in enumerate(_LINES):
        x = 100 + number * _LINE_SIZE * 1.2
        page.insert_text((x, 500), line, fontname="helv", fontsize=_LINE_SIZE, rotate=90)
    path = str(tmp_path / "turned.pdf")
    doc.save(path)
    out = str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [middle] = [span for span in index if span.text == _LINES[1]]
        _apply(engine, [Redact(middle.id)], index)
        engine.save(out)

    left = pymupdf.open(out)[0].get_text()
    assert_in(_LINES[0], left, "the line on one side")
    assert_in(_LINES[2], left, "the line on the other")
    assert_not_in("48,500", left, "the redacted line")


_INK = 128
_ROUND_ONE = "Made on 15 June 2026 between Wescott and Rowe."
_ROUND_TWO = "Signed by Quinn Jakobsz, 16 June 2026."
_ROUND_FACE = "Liberation Serif Regular"


def _inked(path: str, page: int, text: str) -> bool:
    drawn = pymupdf.open(path)[page]
    [box] = drawn.search_for(text)
    return min(drawn.get_pixmap(clip=box).samples) < _INK


def test_an_export_edited_again_keeps_both_rounds_drawn(pdf, tmp_path):
    # Round two must not write into round one's copy, cut to its letters, nor cut it again.
    first, second = str(tmp_path / "first.pdf"), str(tmp_path / "second.pdf")
    with open_pdf(pdf) as engine:
        index = engine.index()
        [made] = [s for s in index if s.text.startswith("Made on")]
        _apply(engine, [Replace(made.id, _ROUND_ONE)], index)
        engine.save(first)
    with open_pdf(first) as engine:
        signed = Insert(REFERENCED_PAGE, (72, 200), _ROUND_TWO, size=11, font=_ROUND_FACE)
        _apply(engine, [signed], engine.index())
        engine.save(second)

    lines = pymupdf.open(second)[REFERENCED_PAGE].get_text().splitlines()
    assert_in(_ROUND_ONE, lines, "the first round's line, read back")
    assert_in(_ROUND_TWO, lines, "the second round's line, read back")
    assert_true(_inked(second, REFERENCED_PAGE, _ROUND_ONE), "the first round's line is drawn")
    assert_true(_inked(second, REFERENCED_PAGE, _ROUND_TWO), "the second round's line is drawn")


def test_a_letter_no_font_has_leaves_the_line_in_its_own_font(engine, tmp_path):
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and s.text.startswith("Invoices"))
    text = span.text.replace("thirty", "中 thirty")
    out = tmp_path / "out.pdf"

    fit = replace_fit(engine, span, text, room_pt=0.0).report
    applied = _apply(engine, [Replace(span.id, text)], index)
    engine.save(str(out))

    drawn = _drawn(out, EMBEDDED_PAGE, "Invoices")
    assert_equal(_squashed(drawn["font"]), _squashed(span.font), "the font that drew it")
    left_out = words.sentence("will_leave_out").format(letters="中")
    assert_equal([words.render(part) for part in fit.describe()], [left_out], "the fit")
    notice = (span.id, words.sentence("left_out").format(letters="中"))
    assert_equal(_said(applied.notices), [notice], "what render tells the user")


_LINKS = {
    "address": "/Border[0 0 2]/C[1 0 0]/A<</S/URI/URI(mailto:sales@example.com)>>",
    # A script, which PyMuPDF's list of links leaves out.
    "script": "/A<</S/JavaScript/JS(print\\(\\))>>",
    "gone": "/Dest[null/XYZ 0 0 0]",
    "reset": "/A<</S/ResetForm>>",
}
_CROPPED = "[20 30 640 900]"


def _linked(path: str, turn_cw: QuarterTurn) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="emb", fontbuffer=face_bytes(FACES["Liberation Sans Regular"]))
    page.insert_text((72, 100), "Contact: sales@example.com", fontname="emb", fontsize=12)
    page.insert_text((72, 200), "Clear the form", fontname="emb", fontsize=12)
    [line] = page.search_for("Contact: sales@example.com")
    third = line.width / 3
    areas = [
        *(
            pymupdf.Rect(line.x0 + i * third, line.y0, line.x0 + (i + 1) * third, line.y1)
            for i in range(3)
        ),
        page.search_for("Clear the form")[0],
    ]
    references = []
    for (name, written), area in zip(_LINKS.items(), areas, strict=True):
        x0, y0, x1, y1 = area * page.transformation_matrix
        link_xref = doc.get_new_xref()
        doc.update_object(
            link_xref,
            f"<</Type/Annot/Subtype/Link/NM({name})/Rect[{x0} {y0} {x1} {y1}]{written}>>",
        )
        references.append(f"{link_xref} 0 R")
    doc.xref_set_key(page.xref, "Annots", "[" + " ".join(references) + "]")
    doc.subset_fonts(verbose=False)
    doc.xref_set_key(page.xref, "CropBox", _CROPPED)
    page.set_rotation(turn_cw)
    doc.save(path)
    return path


def _line_starts(path: str) -> dict[str, tuple[float, float]]:
    blocks = pymupdf.open(path)[0].get_text("dict")["blocks"]
    return {span["text"].split()[0]: span["origin"] for span in each_span(blocks)}


def _annotations_on(path: str) -> list[tuple[str, str]]:
    """In the page's order: it decides which link a click follows where two overlap."""
    doc = pymupdf.open(path)
    return [
        (doc.xref_get_key(xref, "NM")[1], doc.xref_object(xref, compressed=True))
        for xref, _kind, _name in doc[0].annot_xrefs()
    ]


@pytest.mark.parametrize("turn_cw", [0, 90, 180, 270])
@pytest.mark.parametrize(
    ("edit", "kept", "words"),
    [
        ("replace", list(_LINKS), ["Clear", "Contact:", "form", "help@example.com", "the"]),
        ("redact", ["reset"], ["Clear", "form", "the"]),
    ],
    ids=[
        "a replaced line keeps each of its links as it was",
        "a redacted one loses them: a link can carry the text",
    ],
)
def test_an_edit_takes_only_its_line_and_keeps_the_links_it_should(
    tmp_path, edit, kept, words, turn_cw
):
    path = _linked(str(tmp_path / "linked.pdf"), turn_cw)
    out = str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [line] = [span for span in index if span.text.startswith("Contact")]
        change: Edit = (
            Replace(line.id, "Contact: help@example.com")
            if edit == "replace"
            else Redact(line.id)
        )
        _apply(engine, [change], index)
        engine.save(out)

    written = _annotations_on(path)
    expected = [(name, annotation) for name, annotation in written if name in kept]
    assert_equal(
        _annotations_on(out), expected, "each link on the page, in order, as the file writes it"
    )
    said = sorted(pymupdf.open(out)[0].get_text().split())
    assert_equal(said, words, "the edited line's words gone, and the other line's kept")
    original_starts = _line_starts(path)
    for first_word, (x, y) in _line_starts(out).items():
        original_x, original_y = original_starts[first_word]
        assert_close(
            x, original_x, _SAME_START_PT, f"points {first_word!r}'s line moved across"
        )
        assert_close(y, original_y, _SAME_START_PT, f"points {first_word!r}'s line moved down")


@pytest.mark.parametrize(
    ("file_turn", "edit_turn"),
    [(90, None), (180, None), (270, None), (0, 90), (90, 180)],
    ids=["file 90", "file 180", "file 270", "an edit's 90", "file 90 and an edit's 180"],
)
def test_new_text_reads_upright_on_a_page_the_viewer_turns(tmp_path, file_turn, edit_turn):
    doc = pymupdf.open()
    doc.new_page().set_rotation(file_turn)
    path, out = str(tmp_path / "turned.pdf"), str(tmp_path / "out.pdf")
    doc.save(path)
    with open_pdf(path) as engine:
        signed = Insert(0, (200, 400), "Signed", size=12, font="Liberation Sans Regular")
        turns = [] if edit_turn is None else [RotatePage(0, edit_turn)]
        index = engine.index()
        save_edited(
            engine,
            index,
            free_space=engine.free_space(index),
            edits=[signed, *turns],
            to=out,
            driver_factory=DEFAULT_DRIVER_FACTORY,
        )

    page = pymupdf.open(out)[0]
    [line] = [line for block in page.get_text("dict")["blocks"] for line in block["lines"]]
    turn = page.rotation_matrix
    shown = pymupdf.Point(line["dir"]) * turn - pymupdf.Point(0, 0) * turn
    assert_equal(
        (round(shown.x, 2), round(shown.y, 2)), (1.0, 0.0), "the way it reads, as shown"
    )


@dataclass(frozen=True, slots=True)
class _Restyle:
    span_id: str
    text: str
    kind: Literal["restyle"] = "restyle"


def test_an_edit_of_a_kind_nothing_handles_fails_loudly(engine):
    span = _substituted(engine)
    restyle = cast(Edit, _Restyle(span.id, span.text))

    with pytest.raises(AssertionError):
        _apply(engine, [restyle], engine.index())


def _three_pages(path: str) -> str:
    doc = pymupdf.open()
    for line in ("Cover page", "SECRET 4111 2222", "Appendix"):
        doc.new_page().insert_text((72, 96), line, fontname="helv", fontsize=12)
    doc.save(path)
    return path


@dataclass(frozen=True, slots=True)
class _NotErasingDriver:
    driver: PdfDriver

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        return ["" for _box in boxes]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _not_erasing(path: str) -> PdfDriver:
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _NotErasingDriver(open_driver(path)))


def test_the_redaction_check_reads_each_span_where_the_page_order_put_it(tmp_path):
    path, out = _three_pages(str(tmp_path / "three.pdf")), str(tmp_path / "out.pdf")
    with open_pdf(path, driver_factory=_not_erasing) as engine:
        index = engine.index()
        [secret] = [span for span in index if span.text.startswith("SECRET")]
        resolved = _resolved(engine, index, [Redact(secret.id)])
        order = page_order(resolved, [1, 2])
        planned = planned_of(engine, resolved)
        edited = edited_by(planned.steps)
        redactions = redactions_of(engine, resolved, redacted_in(resolved), edited=edited)
        apply_edits(engine, resolved, planned)
        engine.keep_pages(order)
        engine.save(out)

    with pytest.raises(RedactionFailed) as failed:
        redactions.check_saved(out, pages=order, driver_factory=DEFAULT_DRIVER_FACTORY)
    said = words.sentence("redaction_failed").format(text=secret.text, page=2)
    assert_equal(failed.value.detail, said, "what the user reads, naming its original page")


@dataclass(frozen=True, slots=True)
class _KeepingDriver:
    driver: PdfDriver
    at: tuple[float, float]

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        kept = [box for box in boxes if self._holds(box)]
        still = iter(self.driver.text_in(page, kept))
        left = iter(
            self.driver.erase_text(page, [box for box in boxes if not self._holds(box)])
        )
        return [next(still) if self._holds(box) else next(left) for box in boxes]

    def _holds(self, box: Rect) -> bool:
        x, y = self.at
        return box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def test_a_line_one_of_whose_runs_the_erase_cannot_clear_is_put_back_as_it_was(tmp_path):
    path, out = str(tmp_path / "line.pdf"), str(tmp_path / "out.pdf")
    doc = pymupdf.open()
    places = write_runs(doc.new_page(), SENTENCE, origin=(72, 100), size=10)
    doc.save(path)
    in_the_bold_word = ((places[1] + places[2]) / 2, 96)
    keeping = partial(_keeping, at=in_the_bold_word)

    with open_pdf(path, driver_factory=keeping) as engine:
        index = engine.index()
        spans = sorted(index, key=lambda span: span.bbox.x0)
        typed = ["Text is now grouped into ", "spans", spans[2].text]
        runs = tuple(RunText(span.id, text) for span, text in zip(spans, typed, strict=True))
        applied = _apply(engine, [ReplaceRuns(runs)], index)
        engine.save(out)

    stuck = [SpanNotice(spans[1].id, Message("line_not_edited"))]
    assert_equal(applied.notices, stuck, "what's said of the line")
    assert_equal(applied.replaced, [], "an edit counted as drawn")
    blocks = pymupdf.open(out)[0].get_text("dict")["blocks"]
    drawn = sorted(each_span(blocks), key=lambda span: span["origin"][0])
    starts = [(span["text"], round(span["origin"][0], 1)) for span in drawn]
    as_it_was = [
        (text, round(x, 1)) for (text, _font), x in zip(SENTENCE, places[:-1], strict=True)
    ]
    assert_equal(starts, as_it_was, "each run's words, where the page drew them")


def _keeping(path: str, *, at: tuple[float, float]) -> PdfDriver:
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _KeepingDriver(open_driver(path), at))


# The browser's tests read these cases too.
_PAGE_ORDER = json.loads(Path(__file__).with_name("page_order_cases.json").read_text())


def _blank_pages(path: str, count: int) -> str:
    doc = pymupdf.open()
    for _ in range(count):
        doc.new_page()
    doc.save(path)
    return path


@pytest.mark.parametrize("case", _PAGE_ORDER["cases"], ids=lambda case: case["what"])
def test_the_pages_kept_are_the_ones_the_browser_shows(case, tmp_path):
    edits = [DeletePage(edit["page"]) for edit in case["edits"]]
    with open_pdf(_blank_pages(str(tmp_path / "pages.pdf"), case["page_count"])) as engine:
        resolved = _resolved(engine, engine.index(), edits)
    assert_equal(page_order(resolved, None), case["order"], "the pages the download keeps")
