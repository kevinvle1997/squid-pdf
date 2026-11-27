from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from itertools import pairwise
from statistics import median
from typing import Any, cast

import pymupdf
import pytest
from fontTools.ttLib import TTFont

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
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from tests.conftest import (
    REFERENCED_PAGE,
    TYPE3_TEXTS,
    drawn_with,
    each_letter,
    each_span,
    named_only,
    saved_as,
)
from tests.core.conftest import LIST_MARKER, MERGED_TEXTS, TABULAR_PERIOD, coded_under
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
_SERIF_FLAGS = 2 | 32

_POOLED = "Yearly Hello"
_SAME_PT = 0.01  # how far measure() and the listed widths may part: rounding, no more
_INK_DPI = 144  # fine enough that a 14 pt letter's middle covers many pixels
_INK_LEVEL = 128

_ADVANCES = {"A": 500.0, "B": 550.0, " ": 250.0}


def _drawn(path: str) -> list[dict]:
    return _drawn_on(pymupdf.open(path)[0])


def _drawn_on(page: pymupdf.Page) -> list[dict]:
    return list(each_span(page.get_text("rawdict")["blocks"]))


def _text(span: dict) -> str:
    return "".join(c["c"] for c in span["chars"])


def _font_objects(path: str) -> list[tuple[str, str, str]]:
    """Not object numbers: saving renumbers them."""
    fonts = pymupdf.open(path)[0].get_fonts(full=True)
    return sorted((name, kind, file_type) for _xref, file_type, kind, name, *_ in fonts)


def _said(report: FidelityReport) -> str | None:
    return None if report.why is None else words.render(report.why)


def _describe_report(reports: dict[str, FidelityReport]) -> Callable[[Span], str]:
    return lambda s: f"{s.text!r} -> {reports[s.id].state}"


def test_referenced_font_is_a_substitution(engine):
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    referenced = [s for s in engine.index() if s.page == REFERENCED_PAGE]
    describe = _describe_report(reports)
    assert_all(referenced, lambda s: reports[s.id].state == "substitute", describe)
    look_alikes = {
        "Times-Bold": "Liberation Serif Bold",
        "Times-Roman": "Liberation Serif Regular",
    }
    assert_all(referenced, lambda s: reports[s.id].substitute == look_alikes[s.font], describe)
    assert_all(referenced, lambda s: reports[s.id].same_widths, describe)
    not_stored = words.sentence("font_not_in_file")
    assert_all(referenced, lambda s: _said(reports[s.id]) == not_stored, describe)


@pytest.mark.parametrize(
    ("base_font", "flags", "italic_angle", "face", "same_widths"),
    [
        ("Calibri-Bold", None, 0, "Carlito Bold", True),
        ("Poppins-Bold", None, 0, "Poppins Bold", True),
        ("CMBX10", None, 0, "Latin Modern Roman 10 Bold", True),
        ("IBMPlexSerif-SmBld", None, 0, "IBM Plex Serif Bold", False),
        ("NimbusSomething", _SERIF_FLAGS, 0, "Liberation Serif Regular", False),
        ("NimbusSomething", None, 0, "Liberation Sans Regular", False),
        ("NimbusSomething", _SERIF_FLAGS, -11.5, "Liberation Serif Italic", False),
    ],
)
def test_a_font_only_named_is_redrawn_in_its_look_alike_in_its_own_style(
    tmp_path, base_font, flags, italic_angle, face, same_widths
):
    path = named_only(str(tmp_path / "named.pdf"), base_font, flags, italic_angle=italic_angle)
    out = str(tmp_path / "redrawn.pdf")
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


def _serif_described_as_plain(path: str, *, keeps_os2: bool) -> str:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="q", fontbuffer=face_bytes(FACES["PT Serif Regular"]))
    page.insert_text((72, 96), "Hello there", fontname="q", fontsize=_SIZE)
    doc.subset_fonts()
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "BaseFont")[0] == "name":
            doc.xref_set_key(xref, "BaseFont", "/QQQQQQ+Quillon-Book")
        if doc.xref_get_key(xref, "Type")[1] == "/FontDescriptor":
            doc.xref_set_key(xref, "FontName", "/QQQQQQ+Quillon-Book")
            doc.xref_set_key(xref, "Flags", "32")
            if not keeps_os2:
                file = int(doc.xref_get_key(xref, "FontFile2")[1].split()[0])
                doc.update_stream(file, _without_os2(doc.xref_stream(file)))
    doc.save(path)
    return path


def _without_os2(file: bytes) -> bytes:
    font = TTFont(io.BytesIO(file))
    del font["OS/2"]
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


@pytest.mark.parametrize("keeps_os2", [True, False], ids=["its PANOSE", "its letters' shapes"])
def test_a_serif_its_pdf_doesnt_call_serif_is_redrawn_in_a_serif(tmp_path, keeps_os2):
    path = _serif_described_as_plain(str(tmp_path / "plain.pdf"), keeps_os2=keeps_os2)
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(path) as engine:
        span = next(iter(engine.index()))
        engine.remove([span], then_drawn=[LineToDraw(span, "Zed")])
        engine.draw(span, "Zed")
        engine.save(out)

    [drawn] = _drawn(out)
    expected = ("Zed", saved_as("Liberation Serif Regular"))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def test_an_embedded_font_nothing_can_map_through_is_a_substitute(symbolic, tmp_path):
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
    assert_equal(report.substitute, "Liberation Sans Regular", "the face it names")
    first_drawn = _drawn(str(out))[0]
    assert_equal(first_drawn["font"], saved_as("Liberation Sans Regular"), "what redrew it")


def test_a_font_mupdf_cannot_open_is_a_substitute_not_a_crash(corrupt, tmp_path):
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


def test_a_type3_font_with_no_letter_list_is_a_substitute_in_its_own_weight_saying_why(
    type3, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(type3) as engine:
        span = next(iter(engine.index()))
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "Hi there")])
        engine.draw(span, "Hi there")
        engine.save(out)

    expected = ("substitute", words.sentence("font_no_letter_list"))
    assert_equal((report.state, _said(report)), expected, "fidelity, and why")
    assert_equal(
        report.substitute, "Liberation Sans Bold", "the face it names, Bold as described"
    )
    [drawn] = _drawn(out)
    expected_drawn = ("Hi there", saved_as("Liberation Sans Bold"))
    assert_equal((_text(drawn), drawn["font"]), expected_drawn, "what redrew, and in what")


def test_a_type3_font_is_exact_and_redraws_in_its_drawings_and_its_other_copies(
    type3_copies, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    hello, yellow = TYPE3_TEXTS
    with open_pdf(type3_copies) as engine:
        span = next(span for span in engine.index() if span.text == hello)
        _x, baseline = span.origin
        report = next(r for r in engine.assess(engine.index()) if r.span_id == span.id)
        missing = engine.fit.plan_for(span, yellow).missing
        engine.remove([span], then_drawn=[LineToDraw(span, yellow)])
        engine.draw(span, yellow)
        engine.save(out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    saved = pymupdf.open(out)[0]
    assert_equal(saved.get_text().split(), [yellow, yellow], "the lines read back")
    assert_equal(_blank_letters(saved), [], "letters drawn with no ink")
    kinds = [kind for _xref, _ext, kind, *_ in saved.get_fonts()]
    assert_equal(kinds, ["Type3", "Type3"], "the saved page's fonts: the file's two copies")
    fonts = {span["font"] for span in _drawn_on(saved)}
    redrawn = {span["font"] for span in _drawn_on(saved) if span["origin"][1] == baseline}
    assert_equal(redrawn, fonts, "the fonts the redraw used: both copies")


def test_a_letter_with_two_codes_is_measured_and_redrawn_in_the_one_the_line_draws(
    tabular_period, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(tabular_period) as engine:
        [span] = engine.index()
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "2.1.")])
        engine.draw(span, "2.1.")
        engine.save(out)

    assert_equal((span.text, report.state), (LIST_MARKER, "exact"), "the marker, and fidelity")
    traced = [char for run in pymupdf.open(out)[0].get_texttrace() for char in run["chars"]]
    periods = [glyph for letter, glyph, _origin, _box in traced if chr(letter) == "."]
    assert_equal(
        periods, [TABULAR_PERIOD, TABULAR_PERIOD], "the codes the periods are drawn in"
    )


def test_a_font_reached_only_by_code_is_exact_and_redraws_in_itself(coded, tmp_path):
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
        assert_equal(
            saved.redaction.still_there([span]), [], "the old text left in the saved file"
        )


@pytest.mark.parametrize(
    "resource",
    ["F#201", "F#23AB", "F#E9"],
    ids=["a space", "a #", "a byte that isn't UTF-8"],
)
def test_a_font_whose_resource_name_has_a_space_or_a_hash_redraws_in_itself(tmp_path, resource):
    path = coded_under(str(tmp_path / "named.pdf"), resource)
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(path) as engine:
        span = next(span for span in engine.index() if span.text == "ABBA")
        engine.remove([span], then_drawn=[LineToDraw(span, "BA AB")])
        engine.draw(span, "BA AB")
        engine.save(out)

    lines = sorted((_text(drawn), drawn["font"]) for drawn in _drawn(out))
    assert_equal(
        lines, [("BA", "Coded"), ("BA AB", "Coded")], "what each line reads, and in what"
    )


def test_widths_by_code_come_from_the_font_dict(coded):
    with open_pdf(coded) as engine:
        span = next(iter(engine.index()))
        assert_equal(engine.widths(span), _ADVANCES, "letters it draws, to their advances")
        width = sum(_ADVANCES[ch] for ch in "BA AB") * _SIZE / _EM
        assert_equal(round(engine.fit.measure(span, "BA AB"), 4), width, "measured width")


def test_a_letter_a_coded_font_lacks_sends_the_run_to_the_substitute(coded, tmp_path):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(coded) as engine:
        span = next(iter(engine.index()))
        missing = engine.fit.plan_for(span, "ABCD").missing
        engine.remove([span], then_drawn=[LineToDraw(span, "ABC")])
        engine.draw(span, "ABC")
        engine.save(out)

    assert_equal(missing, ["C", "D"], "letters it can't draw")
    [drawn] = _drawn(out)
    expected = ("ABC", saved_as("Liberation Sans Regular"))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def test_a_type0_whose_inner_font_is_listed_apart_keeps_its_description(
    type0_listed_apart, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(type0_listed_apart) as engine:
        span = next(iter(engine.index()))
        engine.remove([span], then_drawn=[LineToDraw(span, "ABC")])
        engine.draw(span, "ABC")
        engine.save(out)

    [drawn] = _drawn(out)
    expected = ("ABC", saved_as("Liberation Serif Italic"))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def test_a_type0_whose_inner_font_points_nowhere_doesnt_stop_an_edit(
    type0_pointing_nowhere, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(type0_pointing_nowhere) as engine:
        span = next(span for span in engine.index() if span.text == "ABBA")
        engine.remove([span], then_drawn=[LineToDraw(span, "BA AB")])
        engine.draw(span, "BA AB")
        engine.save(out)

    lines = sorted(_text(drawn) for drawn in _drawn(out))
    assert_equal(lines, ["BA", "BA AB"], "what each line reads")


def _first_span(engine: Engine) -> Span:
    return next(span for span in engine.index() if span.page == 0)


def _font_files(doc: pymupdf.Document, page: int) -> set[bytes]:
    return {doc.extract_font(font_object)[-1] for font_object, *_ in doc[page].get_fonts()}


def _blank_letters(page: pymupdf.Page) -> list[str]:
    blocks = page.get_text("rawdict")["blocks"]
    return [
        char["c"]
        for char in each_letter(blocks)
        if not char["c"].isspace() and not _inked(page, pymupdf.Rect(char["bbox"]))
    ]


def _inked(page: pymupdf.Page, box: pymupdf.Rect) -> bool:
    quarter = box.width / 4
    middle = pymupdf.Rect(box.x0 + quarter, box.y0, box.x1 - quarter, box.y1)
    samples = page.get_pixmap(clip=middle, dpi=_INK_DPI, alpha=False).samples
    return min(samples) < _INK_LEVEL


def _redraw(path: str, out: str) -> tuple[list[str], FidelityReport]:
    with open_pdf(path) as engine:
        span = _first_span(engine)
        missing = engine.fit.plan_for(span, _POOLED).missing
        new = new_text(
            0,
            origin=(72, 200),
            text=_POOLED,
            size=span.size,
            font=span.font,
            font_object=span.font_object,
        )
        [report] = engine.assess(index_of([new]))
        engine.remove([span], then_drawn=[LineToDraw(span, _POOLED)])
        engine.draw(span, _POOLED)
        engine.save(out)
    return missing, report


def _why_new(engine: Engine, span: Span, text: str) -> str | None:
    new = new_text(
        0,
        origin=(72, 200),
        text=text,
        size=span.size,
        font=span.font,
        font_object=span.font_object,
    )
    [report] = engine.assess(index_of([new]))
    return None if report.why is None else report.why.key


def _assert_drawn_in_both_copies(original: str, out: str) -> None:
    saved, merged = pymupdf.open(out), pymupdf.open(original)
    assert_equal(saved[0].get_text().strip(), _POOLED, "the text read back")
    assert_equal(_blank_letters(saved[0]), [], "letters drawn with no ink")
    copies = _font_files(merged, 0) | _font_files(merged, 1)
    assert_equal(len(copies), 2, "copies of the font in the fixture")
    assert_equal(_font_files(saved, 0), copies, "the font files the redraw used")


def test_a_letter_only_another_pages_copy_draws_is_exact_and_redraws_in_the_files_font(
    merged, tmp_path
):
    out = str(tmp_path / "redrawn.pdf")
    missing, report = _redraw(merged, out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    _assert_drawn_in_both_copies(merged, out)


def test_a_copy_whose_shared_letters_are_other_widths_is_not_pooled(merged_unlike):
    doc = pymupdf.open(merged_unlike)
    fonts = doc[0].get_fonts() + doc[1].get_fonts()
    names = {strip_subset(name) for _xref, _ext, _kind, name, *_ in fonts}
    assert_equal(len(names), 1, "names the fixture's two copies go by, prefix aside")
    with open_pdf(merged_unlike) as engine:
        span = _first_span(engine)
        missing = engine.fit.plan_for(span, _POOLED).missing
        listed = engine.widths(span)
        why = _why_new(engine, span, _POOLED)

    assert_equal(missing, ["Y", "a", "y"], "letters Times lacks")
    assert_not_in("Y", listed, "letters the browser is told Times draws")
    assert_equal(why, "copy_other_widths", "why new text in it is a substitute")


def test_a_copy_sharing_too_few_letters_to_check_is_not_pooled(merged_apart):
    with open_pdf(merged_apart) as engine:
        span = _first_span(engine)
        missing = engine.fit.plan_for(span, "Yak Hello").missing
        why = _why_new(engine, span, "Yak Hello")

    assert_equal(missing, ["Y", "a", "k"], "letters page 0's Times lacks")
    assert_equal(why, "copy_too_few_shared", "why new text in it is a substitute")


def test_widths_list_the_pooled_letters_and_measure_agrees(merged):
    with open_pdf(merged) as engine:
        own, other = list(engine.index())
        widths, others = engine.widths(own), engine.widths(other)
        measured = engine.fit.measure(own, _POOLED)

    every_letter = set("".join(MERGED_TEXTS))
    assert_equal(every_letter - widths.keys(), set(), "letters the pooled list leaves out")
    assert_equal(widths["Y"], others["Y"], "Y's width, borrowed and in its own copy")
    listed = sum(widths[ch] for ch in _POOLED) * own.size / _EM
    assert_close(measured, listed, _SAME_PT, "measured against listed width")


def test_a_font_drawn_by_code_borrows_from_a_coded_copy_on_another_page(merged_coded, tmp_path):
    out = str(tmp_path / "redrawn.pdf")
    missing, report = _redraw(merged_coded, out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    _assert_drawn_in_both_copies(merged_coded, out)


@dataclass(frozen=True, slots=True, eq=False)
class _OpeningDriver:
    driver: PdfDriver
    opened: list[bytes]

    def open_font(self, font_file: bytes) -> FontProgram:
        self.opened.append(font_file)
        return self.driver.open_font(font_file)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _opening_driver(path: str, *, opened: list[bytes]) -> PdfDriver:
    noting = _OpeningDriver(open_driver(path), opened=opened)
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, noting)


def test_a_font_every_page_shares_is_read_once(tmp_path):
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

    with open_pdf(path, driver_factory=partial(_opening_driver, opened=opened)) as engine:
        reports = engine.assess(engine.index())

    assert_all(reports, lambda r: r.state == "exact", lambda r: r.span_id)
    assert_equal(len(opened), 1, "times the shared font was opened")


def test_every_way_a_span_can_be_approximate_has_its_sentence():
    for reason in APPROXIMATE_REASONS:
        assert_in(reason, words.CATALOGS[words.ENGLISH], "the ways a span can be approximate")


_HEBREW = {"text": "םולש", "face": "FreeSerif Regular"}
_ARABIC = {"text": "ﺔﻴﺑﺮﻌﻟﺍ", "face": "FreeSerif Regular"}
# 1250 in Arabic digits: numbers, yet they read back reversed.
_ARABIC_DIGITS = {"text": "١٢٥٠", "face": "FreeSerif Regular"}


def test_letter_spaced_text_is_exact_and_a_redraw_sets_its_letters_as_far_apart(tmp_path):
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(drawn_with(str(tmp_path / "spaced.pdf"), setting="1.5 Tc")) as engine:
        [span] = engine.index()
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, "Terms of sale")])
        engine.draw(span, "Terms of sale")
        engine.save(out)

    assert_equal(report.state, "exact", "how the spaced line is judged")
    [drawn] = _drawn(out)
    letters = [char for char in drawn["chars"] if not char["c"].isspace()]
    apart = [
        b["origin"][0] - a["origin"][0] - (a["bbox"][2] - a["bbox"][0])
        for a, b in pairwise(letters)
        if a["origin"][0] < b["origin"][0]
    ]
    assert_equal(_text(drawn), "Terms of sale", "the line read back")
    assert_close(median(apart), 1.5, 0.1, "how far apart the redrawn letters are set")


@pytest.mark.parametrize(
    ("line", "state", "why"),
    [
        ({}, "exact", None),
        ({"setting": "80 Tz"}, "approximate", "spaced_text"),
        ({"rotate": 90}, "approximate", "turned_text"),
        ({"rotate": 180}, "approximate", "turned_text"),
        ({"rotate": 270}, "approximate", "turned_text"),
        (_HEBREW, "approximate", "right_to_left_text"),
        (_ARABIC, "approximate", "right_to_left_text"),
        (_ARABIC_DIGITS, "approximate", "right_to_left_text"),
    ],
    ids=[
        "as its font sets it",
        "narrowed",
        "turned to read upward",
        "turned upside down",
        "turned to read downward",
        "in Hebrew",
        "in Arabic",
        "in Arabic digits",
    ],
)
def test_text_a_redraw_wouldnt_match_is_approximate_not_exact(tmp_path, line, state, why):
    path = drawn_with(str(tmp_path / "line.pdf"), **line)

    with open_pdf(path) as engine:
        [report] = engine.assess(engine.index())

    judged = (report.state, None if report.why is None else report.why.key)
    assert_equal(judged, (state, why), "how the line is judged, and why")


def test_a_line_an_export_redrew_is_still_exact_when_opened_again(pdf, tmp_path):
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
    assert_equal(green_rate(engine.assess(engine.index())), 0.5, "the sample's green rate")
