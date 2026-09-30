"""Google's copy of a font: fetched by name, lending only when its widths agree."""

from __future__ import annotations

import errno
import io
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from squidpdf.core import Fidelity, google, open_pdf
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT
from squidpdf.core.coverage import Coverage
from squidpdf.core.fonts import FACES, face_bytes
from squidpdf.core.google import (
    Download,
    Fetch,
    GoogleFile,
    RetryAt,
    blob_hash,
    family_list,
    fetched,
    google_fonts,
)
from tests.core.conftest import POPPINS, POPPINS_TEXT
from tests.helpers import assert_at_most, assert_equal, assert_false, assert_true

_WANTED = "Yearly Hello"  # Y, a and y aren't in the file's copy of Poppins
_LACKED = ["Y", "a", "y"]
_POPPINS_PATH = "ofl/poppins/Poppins-Regular.ttf"
_EM = 1000
_WIDE = 200  # how much wider the made-up variable font's A is at its heaviest
_DEADLINE_S = 0.1  # the whole-fetch deadline in the hang test
_HANG_S = 2.0  # the longest its fake connection hangs: far past the deadline


def _google(font_file: bytes | None) -> tuple[Fetch, list[GoogleFile]]:
    """A fetch handing back `font_file` for anything asked, and the list of what was asked."""
    asked: list[GoogleFile] = []

    def fetch(file: GoogleFile) -> bytes | None:
        asked.append(file)
        return font_file

    return fetch, asked


def _missing(path: str, fetch: Fetch) -> list[str]:
    """What the fit says the first span can't draw of _WANTED, with `fetch` to Google."""
    with open_pdf(path, fetch=fetch) as eng:
        span = next(iter(eng.index()))
        return eng.missing(span, _WANTED)


def _fetched(file: GoogleFile, folder: Path, download: Download) -> bytes | None:
    """`fetched`, remembering no failure from any call before."""
    return fetched(file, folder=folder, download=download, retry_at={})


def _failing(url: str) -> bytes:
    """A download that never gets through, as a network down does."""
    raise OSError(f"no route to {url}")


def _returning(data: bytes) -> Callable[[str], bytes]:
    """A download that hands back `data` whatever it's asked for."""
    return lambda _url: data


def test_a_letter_no_copy_in_the_file_draws_comes_from_googles_copy_and_is_exact(
    poppins_subset, tmp_path
):
    """The file's Poppins has no Y; Google's has, as wide: exact, and drawn from it."""
    fetch, asked = _google(POPPINS.read_bytes())
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(poppins_subset, fetch=fetch) as eng:
        span = next(iter(eng.index()))
        missing = eng.missing(span, _WANTED)
        [report] = eng.assess(eng.index())
        eng.remove([span])
        eng.draw(span, _WANTED)
        eng.save(out)

    assert_equal((missing, report.state), ([], Fidelity.EXACT), "missing, and fidelity")
    assert_equal([file.path for file in asked], ["ofl/poppins/Poppins-Regular.ttf"], "fetched")
    saved = pymupdf.open(out)
    assert_equal(saved[0].get_text().strip(), _WANTED, "the text read back")
    font_files = [saved.extract_font(xref)[-1] for xref, *_ in saved[0].get_fonts()]
    lending = [file for file in font_files if Coverage(file).covers("Y")]
    assert_equal(len(lending), 1, "font files on the page that draw a Y")
    # Cut down to what was drawn, as a face we ship is: not the whole font.
    assert_true(len(lending[0]) < len(POPPINS.read_bytes()), "Google's copy was trimmed")


def test_a_google_copy_with_other_widths_lends_nothing(poppins_subset):
    """Liberation Sans handed back as Poppins: its letters are other widths, so none lend."""
    fetch, _asked = _google(face_bytes(FACES["Liberation Sans Regular"]))
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")


def test_a_fetch_that_fails_leaves_the_line_to_the_stand_in_and_is_logged(
    poppins_subset, tmp_path, caplog
):
    """No Google copy to be had: the letters stay missing, and nothing is raised."""
    fetch, _asked = _google(None)
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")

    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(POPPINS.read_bytes()), None)
    with caplog.at_level(logging.WARNING):
        got = _fetched(file, tmp_path, _failing)
    assert_equal(got, None, "what a failed fetch hands back")
    assert_true("fetch failed" in caplog.text, "the failure was logged")


def test_bytes_the_pinned_commit_doesnt_have_are_not_used_or_kept(tmp_path):
    """A download is checked against git's hash for the file; only a match is kept."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    tampered = _fetched(file, tmp_path, _returning(b"not the font"))
    assert_equal(tampered, None, "what bytes of another hash give")
    assert_false(any(tmp_path.rglob("*.ttf")), "anything kept after a mismatch")

    got = _fetched(file, tmp_path, _returning(poppins))
    assert_equal(got, poppins, "what the pinned file's own bytes give")
    # Kept: the next ask is answered from disk, with the network down.
    assert_equal(_fetched(file, tmp_path, _failing), poppins, "from the cache")


def test_a_cache_that_cant_be_written_still_lends_the_copy_and_leaves_no_piece(
    tmp_path, monkeypatch, caplog
):
    """A full disk: the checked copy still lends, the failure is logged, and no piece stays."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    def disk_full(*_args: object) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(os, "replace", disk_full)
    with caplog.at_level(logging.WARNING):
        got = _fetched(file, tmp_path, _returning(poppins))

    assert_true(got == poppins, "the checked download was handed back")
    assert_true("not cached" in caplog.text, "the failure was logged")
    assert_equal([path for path in tmp_path.rglob("*") if path.is_file()], [], "files left")


def test_a_damaged_cached_copy_is_deleted_logged_and_fetched_again(tmp_path, caplog):
    """An empty file, as a crash between writing and flushing leaves, is never trusted."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"")

    with caplog.at_level(logging.WARNING):
        got = _fetched(file, tmp_path, _returning(poppins))

    assert_true(got == poppins, "Google's copy was handed back")
    assert_true("damaged" in caplog.text, "the bad copy was logged")
    assert_true(cached.read_bytes() == poppins, "the cache holds Google's copy after")


def _square(width: int) -> Glyph:
    """A plain filled box `width` wide: the test only measures how wide A is."""
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 700))
    pen.lineTo((width, 700))
    pen.lineTo((width, 0))
    pen.closePath()
    return pen.glyph()


def _variable_font() -> bytes:
    """A made-up variable font with one letter, A: 500 wide at weight 400, wider when bolder.

    The weight axis runs 100 to 900, default 400. Its one variation moves A's
    right edge and its advance (the third, fourth and sixth points: two corners,
    then the right side's phantom point) out by _WIDE at 900, so A is 620 wide
    at 700.
    """
    fb = FontBuilder(_EM, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({ord("A"): "A"})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": _square(500)})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Madeup", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar(axes=[("wght", 100, 400, 900, "Weight")], instances=[])
    moved = [(0, 0), (0, 0), (_WIDE, 0), (_WIDE, 0), (0, 0), (_WIDE, 0), (0, 0), (0, 0)]
    fb.setupGvar({"A": [TupleVariation({"wght": (0, 1, 1)}, moved)]})
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


def test_a_variable_font_is_cut_to_the_weight_and_the_cut_is_checked_when_read(tmp_path):
    """Cut once and cached; a cached cut that's gone bad is deleted, not drawn with."""
    variable = _variable_font()
    file = GoogleFile("ofl/madeup/Madeup[wght].ttf", blob_hash(variable), 700)

    got = _fetched(file, tmp_path, _returning(variable))
    if got is None:
        pytest.fail("no copy was had")
    cut = TTFont(io.BytesIO(got))
    assert_false("fvar" in cut, "the cut still varies")
    assert_equal(cut["hmtx"]["A"][0], 620, "A's width at weight 700")
    again = _fetched(file, tmp_path, _failing)
    assert_true(again == got, "the cut is read back from the cache")

    (tmp_path / GOOGLE_FONTS_COMMIT / file.source).write_bytes(got[:-1])
    assert_equal(_fetched(file, tmp_path, _failing), None, "a cut gone bad")


def test_a_font_google_doesnt_have_is_never_fetched(pdf):
    """The sample's fonts aren't Google's: no fetch, whatever letters are missing."""
    fetch, asked = _google(POPPINS.read_bytes())
    with open_pdf(pdf, fetch=fetch) as eng:
        for span in eng.index():
            eng.missing(span, "Ωxyzq")
    assert_equal(asked, [], "files fetched")


def test_the_poppins_fixture_is_google_s_file_and_draws_only_its_line():
    """Guards the fixtures above: the test font is the pinned file, the trim keeps one line."""
    listed = "0bda228ade88b0bb5aac7da2c881d0c3f64d0817"  # Poppins-Regular.ttf at the pin
    assert_equal(blob_hash(POPPINS.read_bytes()), listed, "the test font's git hash")
    assert_false(any(ch in POPPINS_TEXT for ch in _LACKED), "the line uses a lacked letter")


def test_the_vendored_family_list_was_read_from_the_pinned_commit():
    """A new pin without a new list would fail every changed file's hash check, quietly."""
    assert_equal(family_list()["commit"], GOOGLE_FONTS_COMMIT, "the list's commit")


def test_nothing_is_fetched_when_told_not_to():
    """The suite sets SQUIDPDF_NO_FETCH, so no test, and no worker, reaches the network."""
    assert_equal(google_fonts(), None, "the way to Google's copies")


def test_a_failed_fetch_is_not_tried_again_for_a_while(tmp_path):
    """Remembered per process: an analysis, then every render, would each wait on it again."""
    asked: list[str] = []

    def failing(url: str) -> bytes:
        asked.append(url)
        return _failing(url)

    file = GoogleFile(_POPPINS_PATH, blob_hash(POPPINS.read_bytes()), None)
    retry_at: RetryAt = {}
    for _ in range(3):
        fetched(file, folder=tmp_path, download=failing, retry_at=retry_at)
    assert_equal(len(asked), 1, "downloads tried")


def test_a_fetch_with_no_answer_holds_back_every_file_and_keeps_what_comes_later(
    tmp_path, monkeypatch
):
    """GitHub not answering: one wait, not one per font, and a late answer is still kept."""
    poppins = POPPINS.read_bytes()
    released = threading.Event()
    asked: list[str] = []

    def hanging(url: str) -> bytes:
        asked.append(url)
        released.wait(_HANG_S)
        return poppins

    monkeypatch.setattr(google, "FETCH_TIMEOUT_S", _DEADLINE_S)
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    other = GoogleFile("ofl/poppins/Poppins-Bold.ttf", "a hash never asked for", None)
    retry_at: RetryAt = {}
    started = time.monotonic()
    got = [
        fetched(each, folder=tmp_path, download=hanging, retry_at=retry_at)
        for each in (file, other)
    ]
    assert_at_most(time.monotonic() - started, _HANG_S / 2, "seconds waited")
    assert_equal((got, len(asked)), ([None, None], 1), "what came, and downloads tried")

    released.set()
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    deadline = time.monotonic() + _HANG_S
    while not cached.exists() and time.monotonic() < deadline:
        time.sleep(_DEADLINE_S)
    late = fetched(file, folder=tmp_path, download=_failing, retry_at=retry_at)
    assert_true(late == poppins, "the late answer was kept, and is read from the cache")
