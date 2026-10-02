"""Analysis runs once per build, over the index built from the original; only it downloads."""

from __future__ import annotations

import io
import shutil
import threading
from pathlib import Path

import pymupdf
import pytest
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import Engine
from squidpdf.core.constants import FETCH_TIMEOUT_S
from squidpdf.core.fonts import google
from squidpdf.core.fonts.google import Download
from squidpdf.documents import analyse, store
from squidpdf.documents.constants import ANALYSE_TIMEOUT_S, MAX_PAGES
from squidpdf.documents.errors import TooManyPages
from tests.conftest import drawn_with
from tests.core.conftest import POPPINS
from tests.helpers import assert_at_most, assert_equal, assert_true

# Three of Google's families, each its own file there; Montserrat's is variable, so it'd be cut.
_FAMILIES = ("Poppins-Regular", "Lato-Regular", "Montserrat-Regular")
_WANTED = "Yearly Hello"  # Y, a and y are in no trimmed copy
_DEADLINE_S = 0.1  # the fetch deadline in these tests, for GitHub's 5 s
_HANG_S = 2.0  # the longest a hanging download hangs: far past the deadline


@pytest.fixture
def folder(pdf):
    """A stored document holding the sample as its original."""
    _, folder = store.create("owner")
    shutil.copy(pdf, folder / store.ORIGINAL)
    return folder


@pytest.fixture
def in_google_families():
    """A stored document with one line in each of three Google families.

    Each is a copy of Poppins trimmed to its line, then named as the family: only
    the name picks Google's file. Each line has a letter of its own, so no two
    copies read as one font. None of them has a Y, so each looks to Google's copy.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    for number in range(len(_FAMILIES)):
        line = "Hello there " + "bcd"[number]
        page.insert_font(fontname=f"f{number}", fontbuffer=_trimmed(line))
        page.insert_text((72, 96 + 20 * number), line, fontname=f"f{number}", fontsize=12)
    for (xref, *_), family in zip(page.get_fonts(), _FAMILIES, strict=True):
        # The font, and the one inside it, as a real trimmed copy is named.
        _kind, descendants = doc.xref_get_key(xref, "DescendantFonts")
        for font_xref in (xref, int(descendants.strip("[]").split()[0])):
            doc.xref_set_key(font_xref, "BaseFont", f"/ABCDEF+{family}")
    _, folder = store.create("owner")
    doc.save(folder / store.ORIGINAL)
    return folder


def _trimmed(line: str) -> bytes:
    """Poppins cut down to the letters `line` draws."""
    trimmer = Subsetter(Options())
    trimmer.populate(text=line)
    font = TTFont(POPPINS)
    trimmer.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _google_through(monkeypatch, download: Download) -> list[str]:
    """Let this test reach Google through `download`, fresh as a new worker; what it's asked."""
    asked: list[str] = []

    def counted(url: str) -> bytes:
        asked.append(url)
        return download(url)

    monkeypatch.delenv("SQUIDPDF_NO_FETCH", raising=False)
    monkeypatch.setattr(google, "download", counted)
    monkeypatch.setattr(google, "_retry_at", {})
    monkeypatch.setattr(google, "FETCH_TIMEOUT_S", _DEADLINE_S)
    return asked


def _render_fit(folder: Path, family: str) -> list[str]:
    """What a render's fit says the line in `family` can't draw of _WANTED."""
    with store.open_original(folder) as engine:
        span = next(span for span in engine.index() if span.font.endswith(family))
        return engine.plan_for(span, _WANTED).missing


def test_with_github_not_answering_an_upload_waits_once_and_a_render_never(
    in_google_families, monkeypatch
):
    """One fetch deadline however many families, inside the analysis's; renders never fetch."""
    released = threading.Event()

    def hanging(url: str) -> bytes:
        released.wait(_HANG_S)
        raise TimeoutError(url)

    try:
        asked = _google_through(monkeypatch, hanging)
        analyse.analyse(str(in_google_families), MAX_PAGES)
        assert_equal(len(asked), 1, "downloads waited on by the analysis")
        assert_at_most(FETCH_TIMEOUT_S, ANALYSE_TIMEOUT_S, "one wait, in the analysis's time")

        asked = _google_through(monkeypatch, hanging)
        missing = _render_fit(in_google_families, _FAMILIES[0])
        assert_equal(
            (missing, asked), (["Y", "a", "y"], []), "a render's fit, and its downloads"
        )
    finally:
        released.set()


def test_a_render_lends_what_the_analysis_fetched_and_no_more(in_google_families, monkeypatch):
    """Only analysis downloads, into the store's own cache; a render reads that cache alone."""
    poppins = POPPINS.read_bytes()
    _google_through(monkeypatch, lambda _url: poppins)  # only Poppins' file has its hash
    analyse.analyse(str(in_google_families), MAX_PAGES)
    cached = sorted(path.name for path in (store.root() / "fonts").rglob("*.ttf"))
    assert_equal(cached, ["Poppins-Regular.ttf"], "what the analysis cached")

    asked = _google_through(monkeypatch, lambda _url: poppins)
    fits = [_render_fit(in_google_families, family) for family in _FAMILIES]
    lacked = ["Y", "a", "y"]
    assert_equal(fits, [[], lacked, lacked], "what each family's line can't draw, at render")
    assert_equal(asked, [], "downloads at render")


def test_a_new_build_judges_the_saved_index_never_a_new_one(folder, monkeypatch):
    first = analyse.analyse(str(folder), MAX_PAGES)

    def reindex(self):
        """Stands in for the engine's index, to fail if anything builds one."""
        raise AssertionError("the index was rebuilt")

    monkeypatch.setattr(Engine, "index", reindex)
    monkeypatch.setattr(analyse, "BUILD", "a-later-build")
    later = analyse.analyse(str(folder), MAX_PAGES)

    assert_equal(later["build"], "a-later-build", "build of the second analysis")
    first_ids = [s["id"] for s in first["spans"]]
    later_ids = [s["id"] for s in later["spans"]]
    assert_equal(later_ids, first_ids, "span ids across builds")
    kept = store.load_analysis(folder, "a-later-build")
    assert_true(kept is not None, "the later build's analysis wasn't kept")


def test_a_span_that_wont_come_back_as_it_looks_says_how_and_its_font_stays_usable():
    _, folder = store.create("owner")
    drawn_with(str(folder / store.ORIGINAL), setting="1.5 Tc")

    analysis = analyse.analyse(str(folder), MAX_PAGES)

    [span] = analysis["spans"]
    judged = (span["fidelity"], span["why"])
    assert_equal(judged, ("approximate", {"code": "spaced_text", "params": {}}), "the span")
    [font] = analysis["fonts"]
    assert_equal((font["substitute"], font["why"]), (None, None), "its font: nothing stands in")


def test_a_document_past_the_page_limit_is_refused_before_its_pages_are_read(
    folder, monkeypatch
):
    """Counted, not read: a huge file mustn't cost the reading it's refused to save."""

    def read_pages(self):
        """Stands in for reading every page's size, to fail if anything does."""
        raise AssertionError("the pages were read")

    monkeypatch.setattr(Engine, "pages", read_pages)
    with pytest.raises(TooManyPages):
        analyse.analyse(str(folder), 1)  # the sample has two
