"""Whether a span can be edited in its own font, and the one number that tracks it."""

from __future__ import annotations

from collections.abc import Callable

import pymupdf
import pytest

from squidpdf.core import Fidelity, FidelityReport, MuPDFEngine, Span, green_rate, words
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE, named_only, saved_as
from tests.helpers import assert_all, assert_between, assert_equal, assert_true

_EM = 1000
_SIZE = 12
_ORIGIN_TOLERANCE_PT = 0.01
_SERIF_FLAGS = 2 | 32  # a PDF font description's Serif and Nonsymbolic bits

# The fixtures' /Widths and /W, by letter (see conftest.py).
_ADVANCES = {"A": 500.0, "B": 550.0, " ": 250.0}


def _drawn(path: str) -> list[dict]:
    """Every span of text on the saved file's first page, re-read."""
    blocks = pymupdf.open(path)[0].get_text("rawdict")["blocks"]
    # .get: an image block has no lines.
    return [
        span for block in blocks for line in block.get("lines", []) for span in line["spans"]
    ]


def _text(span: dict) -> str:
    """A rawdict span's text, from its characters."""
    return "".join(c["c"] for c in span["chars"])


def _font_objects(path: str) -> list[tuple[str, str, str]]:
    """Each font object the file's first page uses: its name, kind and program's format.

    Not object numbers: saving renumbers them.
    """
    fonts = pymupdf.open(path)[0].get_fonts(full=True)
    return sorted((name, kind, file_type) for _xref, file_type, kind, name, *_ in fonts)


def _describe_report(reports: dict[str, FidelityReport]) -> Callable[[Span], str]:
    """Names a span by its text and the state it was given, for a failure message."""
    return lambda s: f"{s.text!r} -> {reports[s.id].state}"


def test_referenced_font_is_a_substitution(engine):
    """Page 1's fonts are named but not in the file, so edits cannot match."""
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    referenced = [s for s in engine.index() if s.page == REFERENCED_PAGE]
    describe = _describe_report(reports)
    assert_all(referenced, lambda s: reports[s.id].state is Fidelity.SUBSTITUTE, describe)
    # Drawn in the look-alike we ship, in the span's own style, letters as wide as Times'.
    look_alikes = {
        "Times-Bold": "Liberation Serif Bold",
        "Times-Roman": "Liberation Serif Regular",
    }
    assert_all(referenced, lambda s: reports[s.id].substitute == look_alikes[s.font], describe)
    assert_all(referenced, lambda s: reports[s.id].same_widths, describe)
    not_stored = words.FONT_NOT_IN_FILE
    assert_all(referenced, lambda s: reports[s.id].why == not_stored, describe)


@pytest.mark.parametrize(
    ("base_font", "flags", "face", "same_widths"),
    [
        ("Calibri-Bold", None, "Carlito Bold", True),
        ("Cambria,Italic", None, "Caladea Italic", True),
        ("TimesNewRomanPS-BoldItalicMT", None, "Liberation Serif Bold Italic", True),
        ("Calibri-Light", None, "Carlito Regular", False),  # a cut we don't ship
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
    with MuPDFEngine(path) as eng:
        span = next(iter(eng.index()))
        eng.remove([span])
        eng.draw(span, "Hello again")
        eng.save(out)
    with MuPDFEngine(path) as eng:
        [report] = eng.assess(eng.index())

    assert_equal((report.substitute, report.same_widths), (face, same_widths), "the report")
    [drawn] = _drawn(out)
    expected = ("Hello again", saved_as(face))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")


def test_embedded_font_is_exact(engine):
    reports = {r.span_id: r for r in engine.assess(engine.index())}
    embedded = [s for s in engine.index() if s.page == EMBEDDED_PAGE]
    describe = _describe_report(reports)
    assert_all(embedded, lambda s: reports[s.id].state is Fidelity.EXACT, describe)


def test_green_rate_is_the_share_that_keep_their_font(engine):
    rate = green_rate(engine.assess(engine.index()))
    assert_between(rate, 0.0, 1.0, "green rate on a deliberately mixed fixture")


def test_an_embedded_font_nothing_can_map_through_is_a_substitute(symbolic, tmp_path):
    """Called exact, every redraw in it came out as empty boxes."""
    out = tmp_path / "redrawn.pdf"
    with MuPDFEngine(symbolic) as eng:
        span = next(iter(eng.index()))
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, "ABBA")
        eng.save(str(out))

    assert_equal(report.state, Fidelity.SUBSTITUTE, "fidelity of a symbol-cmap span")
    assert_equal(report.why, words.FONT_NO_LETTER_LIST, "why, as the user reads it")
    # Nothing to go on but a plain description, so a plain sans draws it, and says so.
    assert_equal(report.substitute, "Liberation Sans Regular", "the face it names")
    first_drawn = _drawn(str(out))[0]
    assert_equal(first_drawn["font"], saved_as("Liberation Sans Regular"), "what redrew it")


def test_a_font_mupdf_cannot_open_is_a_substitute_not_a_crash(corrupt, tmp_path):
    """Its program is garbage. Judging the page crashed, and took the whole upload with it."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(corrupt) as eng:
        span = next(iter(eng.index()))
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, "ABBA")
        eng.save(out)

    expected = (Fidelity.SUBSTITUTE, words.FONT_UNREADABLE)
    assert_equal((report.state, report.why), expected, "fidelity, and why")
    [drawn] = _drawn(out)
    assert_equal(drawn["font"], saved_as("Liberation Sans Regular"), "what redrew it")


def test_a_font_reached_only_by_code_is_exact_and_redraws_in_itself(coded, tmp_path):
    """No letter can be looked up in it, but its ToUnicode says which code writes each."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, "BA AB")
        eng.save(out)

    assert_equal(report.state, Fidelity.EXACT, "fidelity of a span its own font draws by code")
    [drawn] = _drawn(out)
    assert_equal((_text(drawn), drawn["font"]), ("BA AB", "Coded"), "what redrew, and in what")
    assert_equal(_font_objects(out), _font_objects(coded), "fonts on the page, none added")


def test_a_redraw_by_code_really_removes_the_old_text(coded, tmp_path):
    """Rule 4: re-read the saved file, not the open one."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        eng.remove([span])
        eng.draw(span, "BAB")
        eng.save(out)

    [drawn] = _drawn(out)
    assert_equal(drawn["font"], "Coded", "the font that redrew it")
    with MuPDFEngine(out) as saved:
        assert_true(saved.absent(span), "the old text is gone from the saved file")


def test_a_redraw_by_code_lands_where_the_original_was(coded, tmp_path):
    """On a mediabox not at 0,0, and turned for the Type0."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        eng.remove([span])
        eng.draw(span, span.text)
        eng.save(out)

    [before] = _drawn(coded)
    [after] = _drawn(out)
    assert_equal(after["font"], "Coded", "the font that redrew it")
    x0, y0 = before["chars"][0]["origin"]
    x1, y1 = after["chars"][0]["origin"]
    assert_between(abs(x1 - x0) + abs(y1 - y0), -1, _ORIGIN_TOLERANCE_PT, "first glyph moved")


def test_widths_by_code_come_from_the_font_dict(coded):
    """The browser's live check reads widths(), the server measure(); both read /Widths, /W."""
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        assert_equal(eng.widths(span), _ADVANCES, "letters it draws, to their advances")
        width = sum(_ADVANCES[ch] for ch in "BA AB") * _SIZE / _EM
        assert_equal(round(eng.measure(span, "BA AB"), 4), width, "measured width")


def test_a_letter_a_coded_font_lacks_sends_the_run_to_the_substitute(coded, tmp_path):
    """C's outline was emptied and D has no code: the fit says so, and nothing mixes faces."""
    out = str(tmp_path / "redrawn.pdf")
    with MuPDFEngine(coded) as eng:
        span = next(iter(eng.index()))
        missing = eng.missing(span, "ABCD")
        eng.remove([span])
        eng.draw(span, "ABC")
        eng.save(out)

    assert_equal(missing, ["C", "D"], "letters it can't draw")
    [drawn] = _drawn(out)
    expected = ("ABC", saved_as("Liberation Sans Regular"))
    assert_equal((_text(drawn), drawn["font"]), expected, "what redrew, and in what")
