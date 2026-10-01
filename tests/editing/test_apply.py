"""Turning edits into real changes: replace, redact, and verify."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import pymupdf
import pytest
from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import Engine, Span, SpanIndex, new_text, open_pdf, words
from squidpdf.core.coverage import Coverage
from squidpdf.core.fonts import FACES, face_bytes, strip_subset
from squidpdf.editing import (
    Applied,
    BadReference,
    Edit,
    Insert,
    Notice,
    Redact,
    RedactionConflict,
    RedactionController,
    RedactionFailed,
    Replace,
    apply_edits,
    insert_fit,
    replace_fit,
    resolve,
)
from squidpdf.editing.apply import page_order, redacted_in
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE, named_only, saved_as, stored_file
from tests.helpers import (
    assert_at_least,
    assert_at_most,
    assert_close,
    assert_equal,
    assert_in,
    assert_not_in,
    assert_true,
)

_LONGER = "!!"  # a few points past the original: within reach of shrink and condense
_FAR_LONGER = " and Co. Ltd"  # a fifth past it: too far to condense
_EDGE_PT = 0.5  # how far past the original's end a fitted run may land, in points
_HEIGHT_PT = 0.1  # finer than the shrink changes a line's height, coarser than rounding
_SAME_WIDTH_PT = 0.25  # how far a same-width redraw's ends may move: far below visible
_ONE_EDIT_ADDS_AT_MOST = 20_000  # bytes an edit in one of our faces may add to the file
# A TrueType font's hinting: code that snaps letters to the screen's pixels.
_HINTING = ("fpgm", "prep", "cvt ")


def _apply(engine, edits: Sequence[Edit], index: SpanIndex) -> Applied:
    """Apply `edits` on every page in memory, as export does before it saves."""
    return apply_edits(engine, resolve(engine, edits, index))


def _each_span(blocks: list[dict]) -> Iterator[dict]:
    """Every span of text in get_text's blocks, in reading order."""
    for block in blocks:
        # .get: an image block has no lines.
        for line in block.get("lines", []):
            yield from line["spans"]


def _drawn(path, page: int, needle: str) -> dict:
    """The span on a saved page whose text contains `needle`, as MuPDF reads it back."""
    blocks = pymupdf.open(path)[page].get_text("dict")["blocks"]
    for span in _each_span(blocks):
        if needle in span["text"]:
            return span
    raise LookupError(f"nothing drawn on page {page} contains {needle!r}")


def _said(notices: list[Notice]) -> list[tuple[str | None, str, int | None]]:
    """Each notice as the user reads it in English, with the edit it's about."""
    return [(notice.span_id, words.render(notice.detail), notice.edit) for notice in notices]


def _assert_cut(path, page: int, face: str, text: str) -> None:
    """`face` is stored cut down, still draws `text`, and keeps its hinting."""
    cut = stored_file(str(path), page, face)
    shipped = face_bytes(FACES[face])
    assert_true(len(cut) < len(shipped), f"{face} stored {len(cut)} bytes of {len(shipped)}")
    assert_equal(Coverage(cut).missing(text), [], f"letters {face} lost in the cut")
    kept = [table for table in _HINTING if table in TTFont(io.BytesIO(cut))]
    shipped_hinting = [table for table in _HINTING if table in TTFont(io.BytesIO(shipped))]
    assert_equal(kept, shipped_hinting, f"{face}'s hinting, cut and as shipped")


def _squashed(font: str) -> str:
    """A font's name, subset prefix aside, in lower case with only letters and digits."""
    return "".join(ch for ch in strip_subset(font).lower() if ch.isalnum())


def _cannot_cut(_subsetter: Subsetter, _font: TTFont) -> None:
    """Fails, as fontTools can on an odd font."""
    raise ValueError("fontTools can't cut this font")


def _insert_as_span(insert: Insert) -> Span:
    """An insert as the span it's measured and drawn as."""
    return new_text(
        insert.page,
        origin=insert.origin,
        text=insert.text,
        size=insert.size,
        font=insert.font,
        color=insert.color,
    )


def _substituted(engine) -> Span:
    """A span in a font the file only references, drawn by its look-alike."""
    return next(
        s for s in engine.index() if s.page == REFERENCED_PAGE and s.text.startswith("Made")
    )


def test_redaction_really_removes_the_text(engine, tmp_path):
    """A covering rectangle would pass a visual check and fail this."""
    index = engine.index()
    span = next(s for s in index if s.page == 1)
    edits = [Redact(span.id)]

    _apply(engine, edits, index)
    engine.save(str(tmp_path / "redacted.pdf"))

    redacted = redacted_in(resolve(engine, edits, index))
    verified = RedactionController(redacted).verdicts(engine)
    assert_equal(verified, {span.id: True}, "the in-memory verdict on the redacted span")
    saved = pymupdf.open(tmp_path / "redacted.pdf")
    text = "".join(page.get_text() for page in saved.pages())
    assert_not_in(span.text, text, "the saved page after a redact")
    # Not only the whole line: nothing at all is left where it was.
    box = pymupdf.Rect(span.bbox.x0, span.bbox.y0, span.bbox.x1, span.bbox.y1)
    left = "".join(saved[span.page].get_textbox(box).split())
    assert_equal(left, "", "letters left in the redacted span's box")

    # Editing it afterwards is refused: the order of a list must never bring it back.
    # The browser undoes a redaction by taking it out of the list.
    with pytest.raises(RedactionConflict):
        _apply(engine, [*edits, Replace(span.id, "Services")], index)


def test_text_under_a_black_box_is_not_gone(pdf, tmp_path):
    """What verified redaction is for: covered text is still in the file."""
    with open_pdf(pdf) as eng:
        span = next(iter(eng.index()))
    covered = tmp_path / "covered.pdf"
    doc = pymupdf.open(pdf)
    box = span.bbox
    doc[span.page].draw_rect(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1), fill=(0, 0, 0))
    doc.save(covered)

    with open_pdf(str(covered)) as eng:
        assert_equal(eng.still_there([span]), [span], "text under a black box, still there")
        eng.remove([span])
        assert_equal(eng.still_there([span]), [], "the same text really removed, still there")


def test_redraws_in_one_font_embed_it_once_per_page(engine, tmp_path):
    """A resource per redrawn span piled up on the page."""
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
    page.draw_line((73, 101.5), (148, 101.5))  # just under the baseline, inside the text's box
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
    """The subset has no é. Drawn in it, the letter would be blank.

    No face we ship has 中, so that is left out, and the user is told.
    Only Liberation Serif is cut; the document's own font is left alone.
    """
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and "14 March" in s.text)
    out, unedited = tmp_path / "accented.pdf", tmp_path / "unedited.pdf"

    applied = _apply(engine, [Replace(span.id, "Delivery begins 14 Février 2026中")], index)
    said_on_save = engine.save(str(out))
    with open_pdf(pdf) as plain:
        plain.save(str(unedited))

    drawn = _drawn(out, EMBEDDED_PAGE, "Février")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    left_out = (span.id, words.sentence("left_out").format(letters="中"), None)
    assert_equal(_said(applied.notices), [left_out], "what render tells the user")
    assert_equal(said_on_save, [], "what save tells the user")
    _assert_cut(
        out, EMBEDDED_PAGE, "Liberation Serif Regular", "Delivery begins 14 Février 2026"
    )
    added = out.stat().st_size - unedited.stat().st_size
    assert_at_most(added, _ONE_EDIT_ADDS_AT_MOST, "bytes the edit added to the file")
    # The document's own font still draws the other line, and nothing in it changed.
    own = strip_subset(span.font)
    [own_name] = [f[3] for f in pymupdf.open(pdf)[EMBEDDED_PAGE].get_fonts() if own in f[3]]
    assert_equal(
        stored_file(str(out), EMBEDDED_PAGE, own_name),
        stored_file(pdf, EMBEDDED_PAGE, own_name),
        "the document's own font file, saved and as it came",
    )


def test_a_look_alike_with_the_same_widths_moves_nothing(engine, tmp_path, monkeypatch):
    """Times is only named here; Liberation Serif redraws it and ends where it did.

    Here the face can't be cut, so it goes in whole, and save says so.
    """
    monkeypatch.setattr(Subsetter, "subset", _cannot_cut)
    index = engine.index()
    span = _substituted(engine)
    out = tmp_path / "same.pdf"

    _apply(engine, [Replace(span.id, span.text)], index)
    said_on_save = engine.save(str(out))

    said = words.sentence("face_not_trimmed").format(font="Liberation Serif Regular")
    assert_equal([words.render(m) for m in said_on_save], [said], "what save tells the user")
    stored = stored_file(str(out), REFERENCED_PAGE, "Liberation Serif Regular")
    shipped = face_bytes(FACES["Liberation Serif Regular"])
    assert_true(stored == shipped, "the whole shipped file went in")
    drawn = _drawn(out, REFERENCED_PAGE, "Made on")
    assert_equal(drawn["font"], saved_as("Liberation Serif Regular"), "the font that drew it")
    x0, _y0, x1, _y1 = drawn["bbox"]
    moved = abs(x0 - span.bbox.x0) + abs(x1 - span.bbox.x1)
    assert_at_most(moved, _SAME_WIDTH_PT, "points the line's ends moved")


def test_letters_the_look_alike_lacks_draw_the_whole_line_in_the_broadest_face(tmp_path):
    """Caladea has no Greek, so the line goes to Noto Serif, whole, and the fit said so."""
    path = named_only(str(tmp_path / "cambria.pdf"), "Cambria")
    out = str(tmp_path / "greek.pdf")
    with open_pdf(path) as eng:
        index = eng.index()
        span = next(iter(index))
        fit = replace_fit(eng, span, "Hi Ωμέγα")
        applied = _apply(eng, [Replace(span.id, "Hi Ωμέγα")], index)
        eng.save(out)

    greek = "Ω or μ or έ or γ or α"  # noqa: RUF001 (Greek on purpose: Caladea has none)
    said = words.sentence("missing").format(chars=greek, font="Noto Serif Regular")
    assert_equal(words.render_all(fit.describe()), said, "what the fit says")
    assert_equal(applied.notices, [], "nothing left out")
    drawn = _drawn(out, 0, "Hi")
    expected = ("Hi Ωμέγα", saved_as("Noto Serif Regular"))
    assert_equal((drawn["text"], drawn["font"]), expected, "what drew, and in what")
    _assert_cut(out, 0, "Noto Serif Regular", "Hi Ωμέγα")


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
            words.sentence("missing").format(chars="Ω", font="Noto Sans Bold"),
        ),
        # A name that's neither ours nor on the page: a look-alike, and the fit says so.
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

    fit = insert_fit(engine, insert)
    _apply(engine, [insert], engine.index())
    engine.save(str(out))

    assert_equal(words.render_all(fit.describe()), said, "what the fit says")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    assert_equal(
        (drawn["text"], drawn["font"]), (text, saved_as(face)), "what drew, and in what"
    )
    _assert_cut(out, REFERENCED_PAGE, face, text)


def test_a_space_the_face_lacks_sends_the_line_to_one_that_has_it(engine, tmp_path):
    """Liberation Mono has no narrow no-break space: it drew as .notdef and ran 8 pt long."""
    out = tmp_path / "spaced.pdf"
    text = "15\u202f000 EUR"  # French thousands, with a narrow no-break space
    insert = Insert(REFERENCED_PAGE, (72.0, 700.0), text, 20.0, "Liberation Mono Regular")

    fit = insert_fit(engine, insert)
    measured = engine.measure(_insert_as_span(insert), text)
    _apply(engine, [insert], engine.index())
    engine.save(str(out))

    assert_equal(fit.missing, ["\u202f"], "what the fit says the face lacks")
    said = "no narrow no-break space in this font, so the line is drawn in Noto Sans Regular"
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
    # The same text left long, for its height: the look-alike's box, not the original's.
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
    fit = insert_fit(engine, signed)
    applied = _apply(engine, edits, index)
    engine.save(str(out))

    skipped = [(s.edit, s.type, words.render(s.detail)) for s in applied.skipped]
    assert_equal(skipped, [(0, "bad_reference", words.sentence("no_span"))], "skipped")
    edited = pymupdf.open(out)[REFERENCED_PAGE].get_text()
    assert_in("2 April 2026", edited, "the edit that was good")
    # What the fit promised is what was drawn: Caveat, 中 left out and said so.
    left_out = words.sentence("left_out").format(letters="中")
    assert_equal(_said(applied.notices), [(None, left_out, 2)], "what render says")
    assert_equal(fit.left_out, ["中"], "what the fit said would be left out")
    drawn = _drawn(out, REFERENCED_PAGE, "Signed")
    expected = ("Signed: Zoë", saved_as("Caveat Regular"))
    assert_equal((drawn["text"].strip(), drawn["font"]), expected, "drawn")


def test_a_redaction_pointing_at_nothing_is_an_error(engine):
    """Skipping it would leave the text the user asked to remove."""
    with pytest.raises(BadReference):
        _apply(engine, [Redact("nosuchid")], engine.index())


def _three_lines(path: str, *, spacing: float, font: str) -> str:
    """Three 12 pt lines, `spacing` times their size apart, in one of MuPDF's own fonts.

    Word and LaTeX set lines about 1.15 to 1.2 times their size apart; at that,
    each letter's box (from its font's ascender to its descender) reaches the
    lines above and below.
    """
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


def test_an_edit_leaves_a_touching_word_in_another_font_alone(tmp_path):
    # "Jones" starts half a point inside the colon's box, as kerning leaves it.
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
    # Three lines reading bottom to top, as a margin note or a stamp is set.
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


_INK = 128  # a pixel darker than this, out of 255, is text drawn, not paper
_ROUND_ONE = "Made on 15 June 2026 between Wescott and Rowe."
_ROUND_TWO = "Signed by Quinn Jakobsz, 16 June 2026."  # letters round one didn't use
_ROUND_FACE = "Liberation Serif Regular"  # what draws round one: the page's Times, only named


def _inked(path: str, page: int, text: str) -> bool:
    """Whether `text` on the page puts ink down, not only reads back."""
    drawn = pymupdf.open(path)[page]
    [box] = drawn.search_for(text)
    return min(drawn.get_pixmap(clip=box).samples) < _INK


def test_an_export_edited_again_keeps_both_rounds_drawn(pdf, tmp_path):
    # Round two adds text in the face round one drew in, on the same page. It must
    # not write into round one's copy, cut down to round one's letters, nor cut
    # that copy again to its own.
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
    """No face we ship has 中. Switching the line's font wouldn't draw it either."""
    index = engine.index()
    span = next(s for s in index if s.page == EMBEDDED_PAGE and s.text.startswith("Invoices"))
    text = span.text.replace("thirty", "中 thirty")
    out = tmp_path / "out.pdf"

    fit = replace_fit(engine, span, text)
    applied = _apply(engine, [Replace(span.id, text)], index)
    engine.save(str(out))

    drawn = _drawn(out, EMBEDDED_PAGE, "Invoices")
    # Spelled "NimbusRoman-Regular" once redrawn, "Nimbus Roman Regular" as found.
    assert_equal(_squashed(drawn["font"]), _squashed(span.font), "the font that drew it")
    left_out = words.sentence("will_leave_out").format(letters="中")
    assert_equal([words.render(part) for part in fit.describe()], [left_out], "the fit")
    notice = (span.id, words.sentence("left_out").format(letters="中"), None)
    assert_equal(_said(applied.notices), [notice], "what render tells the user")


def _linked(path: str) -> str:
    """A line whose address is a link, and a link elsewhere on the page."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Contact: sales@example.com", fontname="helv", fontsize=12)
    page.insert_text((72, 200), "Terms online", fontname="helv", fontsize=12)
    for text, uri in (
        ("sales@example.com", "mailto:sales@example.com"),
        ("Terms", "https://x.test"),
    ):
        [area] = page.search_for(text)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": area, "uri": uri})
    doc.save(path)
    return path


@pytest.mark.parametrize(
    ("edit", "links"),
    [
        ("replace", ["mailto:sales@example.com", "https://x.test"]),
        ("redact", ["https://x.test"]),
    ],
    ids=["a replaced line keeps its link", "a redacted one loses it: it can carry the text"],
)
def test_an_edit_keeps_the_links_it_should(tmp_path, edit, links):
    path = _linked(str(tmp_path / "linked.pdf"))
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

    left = [link["uri"] for link in pymupdf.open(out)[0].get_links()]
    assert_equal(sorted(left), sorted(links), "the page's links")


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_new_text_reads_upright_on_a_page_the_viewer_turns(tmp_path, rotation):
    """Page boxes are unrotated; new text is turned back, so it reads as the page is shown."""
    doc = pymupdf.open()
    doc.new_page().set_rotation(rotation)
    path, out = str(tmp_path / "turned.pdf"), str(tmp_path / "out.pdf")
    doc.save(path)
    with open_pdf(path) as engine:
        signed = Insert(0, (200, 400), "Signed", size=12, font="Liberation Sans Regular")
        _apply(engine, [signed], engine.index())
        engine.save(out)

    page = pymupdf.open(out)[0]
    [line] = [line for block in page.get_text("dict")["blocks"] for line in block["lines"]]
    turn = page.rotation_matrix
    shown = pymupdf.Point(line["dir"]) * turn - pymupdf.Point(0, 0) * turn
    assert_equal(
        (round(shown.x, 2), round(shown.y, 2)), (1.0, 0.0), "the way it reads, as shown"
    )


@dataclass(frozen=True, slots=True)
class _Restyle:
    """An edit kind the code doesn't know: a span restyled. The browser can't send it."""

    span_id: str
    text: str
    kind: Literal["restyle"] = "restyle"


def test_an_edit_of_a_kind_nothing_handles_fails_loudly(engine):
    """It used to be erased and never redrawn, and nothing said so.

    mypy names every place a new kind must be handled; this is the same, at run time.
    """
    span = _substituted(engine)
    restyle = cast(Edit, _Restyle(span.id, span.text))

    with pytest.raises(AssertionError):
        _apply(engine, [restyle], engine.index())


def _three_pages(path: str) -> str:
    """Three pages, one line each; the middle one's line is the one to redact."""
    doc = pymupdf.open()
    for line in ("Cover page", "SECRET 4111 2222", "Appendix"):
        doc.new_page().insert_text((72, 96), line, fontname="helv", fontsize=12)
    doc.save(path)
    return path


def test_the_redaction_check_reads_each_span_where_the_page_order_put_it(tmp_path, monkeypatch):
    """Whoever renumbers the pages, the check reads the order worked out once.

    The erase is what's broken here: the text really is still in the file, one
    page earlier than it was, so reading it on its old page would pass.
    """
    monkeypatch.setattr(Engine, "remove", lambda _engine, _spans: None)
    path, out = _three_pages(str(tmp_path / "three.pdf")), str(tmp_path / "out.pdf")
    with open_pdf(path) as engine:
        index = engine.index()
        [secret] = [span for span in index if span.text.startswith("SECRET")]
        resolved = resolve(engine, [Redact(secret.id)], index)
        order = page_order(resolved, [1, 2])  # the cover page left out
        apply_edits(engine, resolved)
        engine.keep_pages(order)
        engine.save(out)

    with pytest.raises(RedactionFailed) as failed:
        RedactionController(redacted_in(resolved)).check_saved(out, pages=order)
    said = words.sentence("redaction_failed").format(text=secret.text, page=2)
    assert_equal(failed.value.detail, said, "what the user reads, naming its original page")
