"""Analysis of an upload: what it judges, what it refuses, and that only it downloads."""

from __future__ import annotations

import io
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import orjson
import pymupdf
import pytest
from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core import ANALYSIS_TUNING, BUILD, Page, constants
from squidpdf.core.constants import FETCH_TIMEOUT_S
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.documents import analyse, store
from squidpdf.documents.constants import ANALYSE_TIMEOUT_S, MAX_PAGES
from squidpdf.documents.errors import TooManyPages
from squidpdf.documents.store import FOLDER_STORE, DocumentStore, FolderStore
from squidpdf.documents.types import KeptAnalysis
from tests.conftest import POPPINS, drawn_with, name_two_byte_font, type3_copies_at
from tests.helpers import assert_at_most, assert_equal

# Three of Google's families, each its own file there; Montserrat's is variable, so it'd be cut.
_FAMILIES = ("Poppins-Regular", "Lato-Regular", "Montserrat-Regular")
_WANTED = "Yearly Hello"  # Y, a and y are in no trimmed copy
_DEADLINE_S = 0.1  # the fetch deadline in these tests, for GitHub's 5 s
_HANG_S = 2.0  # the longest a hanging download hangs: far past the deadline


def _analysed_upload(documents: DocumentStore, folder: Path) -> KeptAnalysis:
    """An upload's analysis, judged on the one worker that indexed it, as a short one is."""
    analysed = analyse.analyse_upload(
        documents, folder, MAX_PAGES, most_shares=1, spans_per_share=1
    )
    if not isinstance(analysed, KeptAnalysis):
        pytest.fail(f"shared out on one worker: {analysed}")
    return analysed


@pytest.fixture
def folder(pdf):
    """A stored document holding the sample as its original."""
    _, folder = FOLDER_STORE.create("owner")
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
    for (font_object, *_), family in zip(page.get_fonts(), _FAMILIES, strict=True):
        name_two_byte_font(doc, font_object, family)
    _, folder = FOLDER_STORE.create("owner")
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


@dataclass(frozen=True, slots=True)
class _Google:
    """A store whose Google is a test's download, and what that download is asked."""

    store: FolderStore
    asked: list[str]


type GoogleThrough = Callable[[Callable[[str], bytes]], _Google]


@pytest.fixture
def google_through(monkeypatch) -> GoogleThrough:
    """Lets a test reach Google through a download of its own, fresh as a new worker."""
    monkeypatch.setattr(constants, "FETCH_TIMEOUT_S", _DEADLINE_S)

    def through(download: Callable[[str], bytes]) -> _Google:
        """A store whose downloads go to `download`, with no failure remembered."""
        asked: list[str] = []

        def counted(url: str) -> bytes:
            asked.append(url)
            return download(url)

        return _Google(replace(FOLDER_STORE, google_download=counted), asked)

    return through


def _render_fit(documents: FolderStore, folder: Path, family: str) -> list[str]:
    """What a render's fit says the line in `family` can't draw of _WANTED."""
    with documents.open_original(folder) as engine:
        span = next(span for span in engine.index() if span.font.endswith(family))
        return engine.fit.plan_for(span, _WANTED).missing


def test_with_github_not_answering_an_upload_waits_once_and_a_render_never(
    in_google_families, google_through
):
    """One fetch deadline however many families, inside the analysis's; renders never fetch."""
    released = threading.Event()

    def hanging(url: str) -> bytes:
        released.wait(_HANG_S)
        raise TimeoutError(url)

    try:
        google = google_through(hanging)
        _analysed_upload(google.store, in_google_families)
        assert_equal(len(google.asked), 1, "downloads waited on by the analysis")
        assert_at_most(FETCH_TIMEOUT_S, ANALYSE_TIMEOUT_S, "one wait, in the analysis's time")

        google = google_through(hanging)
        missing = _render_fit(google.store, in_google_families, _FAMILIES[0])
        assert_equal(
            (missing, google.asked), (["Y", "a", "y"], []), "a render's fit, and its downloads"
        )
    finally:
        released.set()


def test_a_render_lends_what_the_analysis_fetched_and_no_more(
    in_google_families, google_through
):
    """Only analysis downloads, into the store's own cache; a render reads that cache alone."""
    poppins = POPPINS.read_bytes()
    google = google_through(lambda _url: poppins)  # only Poppins' file has its hash
    _analysed_upload(google.store, in_google_families)
    cached = sorted(path.name for path in (store.root() / "fonts").rglob("*.ttf"))
    assert_equal(cached, ["Poppins-Regular.ttf"], "what the analysis cached")

    google = google_through(lambda _url: poppins)
    fits = [_render_fit(google.store, in_google_families, family) for family in _FAMILIES]
    lacked = ["Y", "a", "y"]
    assert_equal(fits, [[], lacked, lacked], "what each family's line can't draw, at render")
    assert_equal(google.asked, [], "downloads at render")


def test_in_development_an_analysis_is_kept_under_the_code_that_judged_it(monkeypatch):
    """With SQUIDPDF_DEV_REJUDGE set, one kept by other code is worked out again on its read."""
    _, folder = FOLDER_STORE.create("owner")
    drawn_with(str(folder / store.ORIGINAL))
    _analysed_upload(FOLDER_STORE, folder)
    kept_before = store.load_analysis(folder, BUILD)

    monkeypatch.setenv("SQUIDPDF_DEV_REJUDGE", "1")

    kept = (kept_before is not None, store.load_analysis(folder, BUILD))
    assert_equal(kept, (True, None), "kept without the switch, and found with it")


def test_a_retune_judges_the_saved_index_again(monkeypatch):
    """A retune with no new build judges every document again, each span keeping its id."""
    _, folder = FOLDER_STORE.create("owner")
    drawn_with(str(folder / store.ORIGINAL), setting="80 Tz")
    [before] = orjson.loads(_analysed_upload(FOLDER_STORE, folder).spans)
    loose = 1000.0
    # A deploy's retune: what judges the line, and the tuning its analysis is kept under.
    monkeypatch.setattr(constants, "TOLERANCE_PT", loose)
    monkeypatch.setattr(store, "ANALYSIS_TUNING", ANALYSIS_TUNING | {"tolerance_pt": loose})

    kept = store.load_analysis(folder, BUILD)
    [after] = orjson.loads(analyse.analyse(FOLDER_STORE, folder).spans)

    assert_equal(kept, None, "the analysis kept under the old tuning, as a read finds it")
    judged = [(span["id"], span["fidelity"]) for span in (before, after)]
    assert_equal(judged, [(before["id"], "approximate"), (before["id"], "exact")], "the line")


def test_a_span_that_wont_come_back_as_it_looks_says_how_and_its_font_stays_usable():
    _, folder = FOLDER_STORE.create("owner")
    drawn_with(str(folder / store.ORIGINAL), setting="80 Tz")

    kept = _analysed_upload(FOLDER_STORE, folder)

    [span] = orjson.loads(kept.spans)
    judged = (span["fidelity"], span["why"])
    assert_equal(judged, ("approximate", {"code": "spaced_text", "params": {}}), "the span")
    [font] = orjson.loads(kept.facts)["fonts"]
    assert_equal((font["substitute"], font["why"]), (None, None), "its font: nothing stands in")


def test_two_type3_copies_of_one_font_share_the_name_the_reader_knows_them_by():
    """Text names each Type3 copy by its object; their descriptions name the font they share."""
    _, folder = FOLDER_STORE.create("owner")
    type3_copies_at(str(folder / store.ORIGINAL))

    kept = _analysed_upload(FOLDER_STORE, folder)

    fonts = orjson.loads(kept.facts)["fonts"]
    named = [(font["name"].startswith("Type3 ("), font["shown_name"]) for font in fonts]
    expected = [(True, "Drawn-Regular"), (True, "Drawn-Regular")]
    assert_equal(named, expected, "each copy: named by its object, shown by its font")


@dataclass(frozen=True, slots=True)
class _UnreadPagesDriver:
    """MuPDF's driver, failing if anything reads every page's size."""

    driver: PdfDriver

    def pages(self) -> list[Page]:
        """Stands in for reading every page's size, to fail if anything does."""
        raise AssertionError("the pages were read")

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the real driver answers it."""
        return getattr(self.driver, name)


def _unread_pages(path: str) -> PdfDriver:
    """A driver factory for the store: MuPDF's, failing if the pages' sizes are read."""
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _UnreadPagesDriver(open_driver(path)))


def test_a_document_past_the_page_limit_is_refused_before_its_pages_are_read(folder):
    """Counted, not read: a huge file mustn't cost the reading it's refused to save."""
    with pytest.raises(TooManyPages):
        analyse.analyse_upload(  # the sample has two pages
            FolderStore(_unread_pages), folder, 1, most_shares=1, spans_per_share=1
        )
