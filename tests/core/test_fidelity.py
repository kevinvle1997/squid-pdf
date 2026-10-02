"""Whether a span can be edited in its own font, and the one number that tracks it."""

from __future__ import annotations

from collections.abc import Callable

import pymupdf
import pytest

from squidpdf.core import (
    APPROXIMATE_REASONS,
    Engine,
    FidelityReport,
    LineToDraw,
    Span,
    green_rate,
    index_of,
    new_text,
    open_pdf,
    words,
)
from squidpdf.core.fonts.look_alike import strip_subset
from squidpdf.core.pdf.mupdf import MuPDFDriver, MuPDFFont
from tests.conftest import REFERENCED_PAGE, drawn_with, each_span, named_only, saved_as
from tests.core.conftest import MERGED_TEXTS
from tests.helpers import (
    assert_all,
    assert_at_most,
    assert_close,
    assert_equal,
    assert_in,
    assert_not_in,
)

_EM = 1000
_SIZE = 12
_ORIGIN_TOLERANCE_PT = 0.01
_SERIF_FLAGS = 2 | 32  # a PDF font description's Serif and Nonsymbolic bits

# Every letter drawn by one merged page's copy or the other, and the two alternate:
# Y a y from page 1's copy, the rest from page 0's.
_POOLED = "Yearly Hello"
_SAME_PT = 0.01  # how far measure() and the listed widths may part: rounding, no more
_INK_DPI = 144  # fine enough that a 14 pt letter's middle covers many pixels
_INK_LEVEL = 128  # a pixel darker than mid-grey is ink on the white page

# The fixtures' /Widths and /W, by letter (see conftest.py).
_ADVANCES = {"A": 500.0, "B": 550.0, " ": 250.0}


def _drawn(path: str) -> list[dict]:
    """Every span of text on the saved file's first page, re-read."""
    blocks = pymupdf.open(path)[0].get_text("rawdict")["blocks"]
    return list(each_span(blocks))


def _text(span: dict) -> str:
    """A rawdict span's text, from its characters."""
    return "".join(c["c"] for c in span["chars"])


def _font_objects(path: str) -> list[tuple[str, str, str]]:
    """Each font object the file's first page uses: its name, kind and program's format.

    Not object numbers: saving renumbers them.
    """
    fonts = pymupdf.open(path)[0].get_fonts(full=True)
    return sorted((name, kind, file_type) for _xref, file_type, kind, name, *_ in fonts)


def _said(report: FidelityReport) -> str | None:
    """Why the report says the span's own font can't be used, in English; None when it can."""
    return None if report.why is None else words.render(report.why)


def _describe_report(reports: dict[str, FidelityReport]) -> Callable[[Span], str]:
    """Names a span by its text and the state it was given, for a failure message."""
    return lambda s: f"{s.text!r} -> {reports[s.id].state}"


def test_referenced_font_is_a_substitution(engine):
    """Page 1's fonts are named but not in the file, so edits cannot match."""
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    referenced = [s for s in engine.index() if s.page == REFERENCED_PAGE]
    describe = _describe_report(reports)
    assert_all(referenced, lambda s: reports[s.id].state == "substitute", describe)
    # Drawn in the look-alike we ship, in the span's own style, letters as wide as Times'.
    look_alikes = {
        "Times-Bold": "Liberation Serif Bold",
        "Times-Roman": "Liberation Serif Regular",
    }
    assert_all(referenced, lambda s: reports[s.id].substitute == look_alikes[s.font], describe)
    assert_all(referenced, lambda s: reports[s.id].same_widths, describe)
    not_stored = words.sentence("font_not_in_file")
    assert_all(referenced, lambda s: _said(reports[s.id]) == not_stored, describe)


@pytest.mark.parametrize(
    ("base_font", "flags", "face", "same_widths"),
    [
        ("Calibri-Bold", None, "Carlito Bold", True),
        # The typeface itself, shipped: its own letters, so its own widths.
        ("Poppins-Bold", None, "Poppins Bold", True),
        # A name that says its cut with no dash, as TeX's do: the name alone picks the face.
        ("CMBX10", None, "Latin Modern Roman 10 Bold", True),
        # A font we don't know: its kind comes from the PDF's description, not its name.
        ("NimbusSomething", _SERIF_FLAGS, "Liberation Serif Regular", False),
        ("NimbusSomething", None, "Liberation Sans Regular", False),
    ],
)
def test_a_font_only_named_is_redrawn_in_its_look_alike_in_its_own_style(
    tmp_path, base_font, flags, face, same_widths
):
    """Calibri-Bold gets Carlito Bold, the face the report names, not Helvetica."""
    path = named_only(str(tmp_path / "named.pdf"), base_font, flags)
    out = str(tmp_path / "redrawn.pdf")
    # Drawn with nothing asked first: remove() must read the font before erasing it.
    with open_pdf(path) as engine:
        span = next(iter(engine.index()))
        engine.remove([span], then_drawn=[LineToDraw(span, "Hello again")])
        engine.draw(span, "Hello again")
        engine.save(out)
    with open_pdf(path) as engine:
        [report] = engine.assess(engine.index())

    assert_equal((report.substitute, report.same_widths), (face, same_widths), "the report")
    [drawn] = _drawn(out)
    expected = ("Hello again", saved_as(face))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def test_an_embedded_font_nothing_can_map_through_is_a_substitute(symbolic, tmp_path):
    """Called exact, every redraw in it came out as empty boxes."""
    out = tmp_path / "redrawn.pdf"
    with open_pdf(symbolic) as engine:
        span = next(iter(engine.index()))
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "ABBA")])
        engine.draw(span, "ABBA")
        engine.save(str(out))

    assert_equal(report.state, "substitute", "fidelity of a symbol-cmap span")
    assert_equal(
        _said(report), words.sentence("font_no_letter_list"), "why, as the user reads it"
    )
    # Nothing to go on but a plain description, so a plain sans draws it, and says so.
    assert_equal(report.substitute, "Liberation Sans Regular", "the face it names")
    first_drawn = _drawn(str(out))[0]
    assert_equal(first_drawn["font"], saved_as("Liberation Sans Regular"), "what redrew it")


def test_a_font_mupdf_cannot_open_is_a_substitute_not_a_crash(corrupt, tmp_path):
    """Its program is garbage. Judging the page crashed, and took the whole upload with it."""
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(corrupt) as engine:
        span = next(iter(engine.index()))
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "ABBA")])
        engine.draw(span, "ABBA")
        engine.save(out)

    expected = ("substitute", words.sentence("font_unreadable"))
    assert_equal((report.state, _said(report)), expected, "fidelity, and why")
    [drawn] = _drawn(out)
    assert_equal(drawn["font"], saved_as("Liberation Sans Regular"), "what redrew it")


def test_a_font_reached_only_by_code_is_exact_and_redraws_in_itself(coded, tmp_path):
    """No letter can be looked up in it, but its ToUnicode says which code writes each.

    The redraw starts where the original did, on a mediabox not at 0,0 and, for
    the Type0, turned; and the old text is gone from the saved file (Rule 4).
    """
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(coded) as engine:
        span = next(iter(engine.index()))
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "BA AB")])
        engine.draw(span, "BA AB")
        engine.save(out)

    assert_equal(report.state, "exact", "fidelity of a span its own font draws by code")
    [before] = _drawn(coded)
    [after] = _drawn(out)
    assert_equal((_text(after), after["font"]), ("BA AB", "Coded"), "what redrew, and in what")
    assert_equal(_font_objects(out), _font_objects(coded), "fonts on the page, none added")
    x0, y0 = before["chars"][0]["origin"]
    x1, y1 = after["chars"][0]["origin"]
    assert_at_most(
        abs(x1 - x0) + abs(y1 - y0), _ORIGIN_TOLERANCE_PT, "points the first glyph moved"
    )
    with open_pdf(out) as saved:
        assert_equal(saved.still_there([span]), [], "the old text left in the saved file")


def test_widths_by_code_come_from_the_font_dict(coded):
    """The browser's live check reads widths(), the server measure(); both read /Widths, /W."""
    with open_pdf(coded) as engine:
        span = next(iter(engine.index()))
        assert_equal(engine.widths(span), _ADVANCES, "letters it draws, to their advances")
        width = sum(_ADVANCES[ch] for ch in "BA AB") * _SIZE / _EM
        assert_equal(round(engine.measure(span, "BA AB"), 4), width, "measured width")


def test_a_letter_a_coded_font_lacks_sends_the_run_to_the_substitute(coded, tmp_path):
    """C's outline was emptied and D has no code: the fit says so, and nothing mixes faces."""
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(coded) as engine:
        span = next(iter(engine.index()))
        missing = engine.plan_for(span, "ABCD").missing
        engine.remove([span], then_drawn=[LineToDraw(span, "ABC")])
        engine.draw(span, "ABC")
        engine.save(out)

    assert_equal(missing, ["C", "D"], "letters it can't draw")
    [drawn] = _drawn(out)
    expected = ("ABC", saved_as("Liberation Sans Regular"))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def _first_span(engine: Engine) -> Span:
    """The first span on page 0: the merged fixtures' one line there."""
    return next(span for span in engine.index() if span.page == 0)


def _font_files(doc: pymupdf.Document, page: int) -> set[bytes]:
    """Each font file a page of `doc` draws with."""
    return {doc.extract_font(xref)[-1] for xref, *_ in doc[page].get_fonts()}


def _blank_letters(page: pymupdf.Page) -> list[str]:
    """The letters on a page that put no ink down, in order."""
    blocks = page.get_text("rawdict")["blocks"]
    letters = [char for span in each_span(blocks) for char in span["chars"]]
    return [
        char["c"]
        for char in letters
        if not char["c"].isspace() and not _inked(page, pymupdf.Rect(char["bbox"]))
    ]


def _inked(page: pymupdf.Page, box: pymupdf.Rect) -> bool:
    """Whether anything is drawn in the middle half of `box`, clear of its neighbours."""
    quarter = box.width / 4
    middle = pymupdf.Rect(box.x0 + quarter, box.y0, box.x1 - quarter, box.y1)
    samples = page.get_pixmap(clip=middle, dpi=_INK_DPI, alpha=False).samples
    return min(samples) < _INK_LEVEL


def _redraw(path: str, out: str) -> tuple[list[str], FidelityReport]:
    """Redraw page 0's line as _POOLED: what the fit says is missing, and new text's state."""
    with open_pdf(path) as engine:
        span = _first_span(engine)
        missing = engine.plan_for(span, _POOLED).missing
        new = new_text(0, origin=(72, 200), text=_POOLED, size=span.size, font=span.font)
        [report] = engine.assess(index_of([new]))
        engine.remove([span], then_drawn=[LineToDraw(span, _POOLED)])
        engine.draw(span, _POOLED)
        engine.save(out)
    return missing, report


def _why_new(engine: Engine, span: Span, text: str) -> str | None:
    """Why new text in the span's font would be a substitute, by its sentence's key."""
    new = new_text(0, origin=(72, 200), text=text, size=span.size, font=span.font)
    [report] = engine.assess(index_of([new]))
    return None if report.why is None else report.why.key


def _assert_drawn_in_both_copies(original: str, out: str) -> None:
    """The line reads back whole, every letter inked, drawn in the file's two copies alone."""
    saved, merged = pymupdf.open(out), pymupdf.open(original)
    assert_equal(saved[0].get_text().strip(), _POOLED, "the text read back")
    assert_equal(_blank_letters(saved[0]), [], "letters drawn with no ink")
    copies = _font_files(merged, 0) | _font_files(merged, 1)
    assert_equal(len(copies), 2, "copies of the font in the fixture")
    assert_equal(_font_files(saved, 0), copies, "the font files the redraw used")


def test_a_letter_only_another_pages_copy_draws_is_exact_and_redraws_in_the_files_font(
    merged, tmp_path
):
    """Page 0's Times has no Y; page 1's has. Before, the whole line went to the substitute."""
    out = str(tmp_path / "redrawn.pdf")
    missing, report = _redraw(merged, out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    _assert_drawn_in_both_copies(merged, out)


def test_a_copy_whose_shared_letters_are_other_widths_is_not_pooled(merged_unlike):
    """Helvetica named as Times: its e, l and r are wider, so it lends Times no letter."""
    doc = pymupdf.open(merged_unlike)
    fonts = doc[0].get_fonts() + doc[1].get_fonts()
    names = {strip_subset(name) for _xref, _ext, _kind, name, *_ in fonts}
    assert_equal(len(names), 1, "names the fixture's two copies go by, prefix aside")
    with open_pdf(merged_unlike) as engine:
        span = _first_span(engine)
        missing = engine.plan_for(span, _POOLED).missing
        listed = engine.widths(span)
        why = _why_new(engine, span, _POOLED)

    assert_equal(missing, ["Y", "a", "y"], "letters Times lacks")
    assert_not_in("Y", listed, "letters the browser is told Times draws")
    assert_equal(why, "copy_other_widths", "why new text in it is a substitute")


def test_a_copy_sharing_too_few_letters_to_check_is_not_pooled(merged_apart):
    """Page 1's copy draws Y but no letter page 0 does, so it can't vouch for itself."""
    with open_pdf(merged_apart) as engine:
        span = _first_span(engine)
        missing = engine.plan_for(span, "Yak Hello").missing
        why = _why_new(engine, span, "Yak Hello")

    assert_equal(missing, ["Y", "a", "k"], "letters page 0's Times lacks")
    assert_equal(why, "copy_too_few_shared", "why new text in it is a substitute")


def test_widths_list_the_pooled_letters_and_measure_agrees(merged):
    """The browser's live fit reads widths(), the server measure(): pooled, they must agree."""
    with open_pdf(merged) as engine:
        own, other = list(engine.index())
        widths, others = engine.widths(own), engine.widths(other)
        measured = engine.measure(own, _POOLED)

    every_letter = set("".join(MERGED_TEXTS))
    assert_equal(every_letter - widths.keys(), set(), "letters the pooled list leaves out")
    assert_equal(widths["Y"], others["Y"], "Y's width, borrowed and in its own copy")
    listed = sum(widths[ch] for ch in _POOLED) * own.size / _EM
    assert_close(measured, listed, _SAME_PT, "measured against listed width")


def test_a_font_drawn_by_code_borrows_from_a_coded_copy_on_another_page(merged_coded, tmp_path):
    """Both copies write Y with one code; only page 1's draws it. It joins page 0's fonts."""
    out = str(tmp_path / "redrawn.pdf")
    missing, report = _redraw(merged_coded, out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    _assert_drawn_in_both_copies(merged_coded, out)


def test_a_font_every_page_shares_is_read_once(tmp_path, monkeypatch):
    # Three pages, one stored font object: a long document's usual shape. Trimmed,
    # as a generator leaves it, which also names it the way its spans are named.
    doc = pymupdf.open()
    font_file = pymupdf.Font("tiro").buffer
    for page_number in range(3):
        page = doc.new_page()
        page.insert_font(fontname="emb", fontbuffer=font_file)
        page.insert_text((72, 72), f"Page {page_number} text", fontname="emb", fontsize=_SIZE)
    doc.subset_fonts(verbose=False)
    path = str(tmp_path / "shared.pdf")
    doc.save(path)
    opened: list[bytes] = []
    open_font = MuPDFDriver.open_font

    def counted(self: MuPDFDriver, font_file: bytes) -> MuPDFFont:
        opened.append(font_file)
        return open_font(self, font_file)

    monkeypatch.setattr(MuPDFDriver, "open_font", counted)

    with open_pdf(path) as engine:
        reports = engine.assess(engine.index())

    assert_all(reports, lambda r: r.state == "exact", lambda r: r.span_id)
    assert_equal(len(opened), 1, "times the shared font was opened")


def test_every_way_a_span_can_be_approximate_has_its_sentence():
    """The browser says a span's `why` from `copy.approximate`, keyed by its reason."""
    for reason in APPROXIMATE_REASONS:
        assert_in(reason, words.ENGLISH_SENTENCES, "the ways a span can be approximate")


@pytest.mark.parametrize(
    ("setting", "rotate", "state", "why"),
    [
        ("", 0, "exact", None),
        ("1.5 Tc", 0, "approximate", "spaced_text"),
        ("80 Tz", 0, "approximate", "spaced_text"),
        ("", 90, "approximate", "turned_text"),
    ],
    ids=["as its font sets it", "letter spacing", "narrowed", "turned to read upward"],
)
def test_text_a_redraw_wouldnt_match_is_approximate_not_exact(
    tmp_path, setting, rotate, state, why
):
    """Its own font draws it, but level and closed up: an edit would look different."""
    path = drawn_with(str(tmp_path / "line.pdf"), setting=setting, rotate=rotate)

    with open_pdf(path) as engine:
        [report] = engine.assess(engine.index())

    judged = (report.state, None if report.why is None else report.why.key)
    assert_equal(judged, (state, why), "how the line is judged, and why")


def test_a_line_an_export_redrew_is_still_exact_when_opened_again(pdf, tmp_path):
    """Its text reads "NimbusRoman-Regular", the font file's own name; the page lists it
    as "Nimbus Roman Regular". The same font either way, so the next edit keeps it.
    """
    out = str(tmp_path / "exported.pdf")
    with open_pdf(pdf) as engine:
        index = engine.index()
        [line] = [span for span in index if span.text.startswith("Invoices")]
        typed = "Invoices are due within ten days."
        engine.remove([line], then_drawn=[LineToDraw(line, typed)])
        engine.draw(line, typed)
        engine.save(out)

    with open_pdf(out) as again:
        index = again.index()
        [line] = [span for span in index if span.text.startswith("Invoices")]
        [report] = again.assess(index_of([line]))

    assert_equal((report.state, _said(report)), ("exact", None), "the redrawn line")


def test_the_green_rate_is_the_share_of_spans_that_keep_their_font(engine):
    """The one number tracked: two of the sample's four spans are in a font the file stores."""
    assert_equal(green_rate(engine.assess(engine.index())), 0.5, "the sample's green rate")
    assert_equal(green_rate([]), 0.0, "the green rate of a document with no text")
