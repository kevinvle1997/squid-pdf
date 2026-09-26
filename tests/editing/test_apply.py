"""Turning edits into real changes: replace, redact, and verify."""

from __future__ import annotations

import pymupdf
import pytest

from squidpdf.core import MuPDFEngine, Span, words
from squidpdf.editing import (
    BadReference,
    Edit,
    Insert,
    Notice,
    Redact,
    Replace,
    Skipped,
    apply,
    check,
    check_insert,
    verify_redactions,
)
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE, named_only, saved_as
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_true

_LONGER = "!!"  # a few points past the original: within reach of shrink and condense
_FAR_LONGER = " and Co. Ltd"  # a fifth past it: too far to condense
_EDGE_PT = 0.5  # how far past the original's end a fitted run may land, in points
_HEIGHT_PT = 0.1  # finer than the shrink changes a line's height, coarser than rounding
_SAME_WIDTH_PT = 0.25  # how far a same-width redraw's ends may move: far below visible


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
    """A span in a font the file only references, drawn by its look-alike."""
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

    # Editing it afterwards brings the text back, and render says so.
    undone = apply(engine, [*edits, Replace(span.id, "Services")], index).notices
    assert_equal(undone, [Notice(span.id, words.REDACTION_UNDONE)], "notices after the edit")


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

    No face we ship has 中, so that is left out, and the user is told.
    """
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and "14 March" in s.text)
    out = tmp_path / "accented.pdf"

    applied = apply(engine, [Replace(span.id, "Delivery begins 14 Février 2026中")], index)
    engine.save(str(out))

    drawn = _drawn(out, EMBEDDED_PAGE, "Février")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    left_out = Notice(span.id, words.LEFT_OUT.format(letters="中"))
    assert_equal(applied.notices, [left_out], "what render tells the user")


def test_a_look_alike_with_the_same_widths_moves_nothing(engine, tmp_path):
    """Times is only named here; Liberation Serif redraws it and ends where it did."""
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "same.pdf"

    apply(engine, [Replace(span.id, span.text)], index)
    engine.save(str(out))

    drawn = _drawn(out, REFERENCED_PAGE, "Made on")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    x0, _y0, x1, _y1 = drawn["bbox"]
    moved = abs(x0 - span.bbox.x0) + abs(x1 - span.bbox.x1)
    assert_true(moved < _SAME_WIDTH_PT, f"the line's ends moved {moved:.2f} pt")


def test_letters_the_look_alike_lacks_draw_the_whole_line_in_the_broadest_face(tmp_path):
    """Caladea has no Greek, so the line goes to Noto Serif, whole, and the fit said so."""
    path = named_only(str(tmp_path / "cambria.pdf"), "Cambria")
    out = str(tmp_path / "greek.pdf")
    with MuPDFEngine(path) as eng:
        index = eng.index()
        span = next(iter(index))
        fit = check(eng, span, "Hi Ωμέγα")
        applied = apply(eng, [Replace(span.id, "Hi Ωμέγα")], index)
        eng.save(out)

    said = words.MISSING.format(chars="Ω or μ or έ or γ or α", font="Noto Serif Regular")
    assert_equal(fit.describe(), said, "what the fit says")
    assert_equal(applied.notices, [], "nothing left out")
    drawn = _drawn(out, 0, "Hi")
    expected = ("Hi Ωμέγα", saved_as("Noto Serif Regular"))
    assert_equal((drawn["text"], drawn["font"]), expected, "what drew, and in what")


@pytest.mark.parametrize(
    ("font", "text", "face", "said"),
    [
        # A face we ship draws it, as asked.
        ("Caveat Bold", "Signed", "Caveat Bold", None),
        # It lacks Greek: the whole line goes to the broadest face of its kind.
        (
            "Caveat Bold",
            "Signed Ω",
            "Noto Sans Bold",
            words.MISSING.format(chars="Ω", font="Noto Sans Bold"),
        ),
        # A name that's neither ours nor on the page: a look-alike, and the fit says so.
        (
            "Comic Sans",
            "Signed",
            "Liberation Sans Regular",
            words.CHOSEN_UNAVAILABLE.format(
                chosen="Comic Sans", font="Liberation Sans Regular"
            ),
        ),
    ],
)
def test_new_text_is_drawn_in_the_face_its_fit_names(engine, tmp_path, font, text, face, said):
    out = tmp_path / "inserted.pdf"
    insert = Insert(REFERENCED_PAGE, (72.0, 700.0), text, 12.0, font)

    fit = check_insert(engine, insert)
    apply(engine, [insert], engine.index())
    engine.save(str(out))

    assert_equal(fit.describe(), said, "what the fit says")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    assert_equal(
        (drawn["text"], drawn["font"]), (text, saved_as(face)), "what drew, and in what"
    )


@pytest.mark.parametrize("strategy", ["shrink", "condense"])
def test_a_fitting_strategy_ends_the_run_where_the_original_did(
    engine, pdf, tmp_path, strategy
):
    index = engine.index()
    span = _substituted(engine)
    out, as_is = tmp_path / f"{strategy}.pdf", tmp_path / "as-is.pdf"

    apply(engine, [Replace(span.id, span.text + _LONGER, strategy)], index)
    engine.save(str(out))
    # The same text left long, for its height: the look-alike's box, not the original's.
    with MuPDFEngine(pdf) as plain:
        apply(plain, [Replace(span.id, span.text + _LONGER)], plain.index())
        plain.save(str(as_is))

    drawn = _drawn(out, REFERENCED_PAGE, _LONGER)
    _x0, top, end, bottom = drawn["bbox"]
    assert_true(end <= span.bbox.x1 + _EDGE_PT, f"{strategy} ends at {end}, not {span.bbox.x1}")
    # Height, not size: MuPDF reports a narrowed run's size as smaller too.
    height = bottom - top
    _x0, as_is_top, _x1, as_is_bottom = _drawn(as_is, REFERENCED_PAGE, _LONGER)["bbox"]
    shorter = height < as_is_bottom - as_is_top - _HEIGHT_PT
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

    # New text in the face the user chose; 中 no font of ours can draw.
    signed = Insert(REFERENCED_PAGE, (72.0, 700.0), "Signed: Zoë 中", 12.0, "Caveat Regular")
    edits: list[Edit] = [
        Replace("nosuchid", "x"),
        Replace(span.id, "Made on 2 April 2026."),
        signed,
    ]
    fit = check_insert(engine, signed)
    applied = apply(engine, edits, index)
    engine.save(str(out))

    assert_equal(applied.skipped, [Skipped(0, "bad_reference", words.NO_SPAN)], "skipped")
    edited = pymupdf.open(out)[REFERENCED_PAGE].get_text()
    assert_in("2 April 2026", edited, "the edit that was good")
    # What the fit promised is what was drawn: Caveat, 中 left out and said so.
    left_out = words.LEFT_OUT.format(letters="中")
    assert_equal(applied.notices, [Notice(None, left_out, edit=2)], "what render says")
    assert_equal(fit.left_out, ["中"], "what the fit said would be left out")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    expected = ("Signed: Zoë", saved_as("Caveat Regular"))
    assert_equal((drawn["text"].strip(), drawn["font"]), expected, "drawn")


def test_a_redaction_pointing_at_nothing_is_an_error(engine):
    """Skipping it would leave the text the user asked to remove."""
    with pytest.raises(BadReference):
        apply(engine, [Redact("nosuchid")], engine.index())
