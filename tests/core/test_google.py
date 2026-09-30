"""Google's copy of a font: fetched by name, lending only when its widths agree."""

from __future__ import annotations

import errno
import logging
import os
import time
from collections.abc import Callable

import pymupdf
import pytest

from squidpdf.core import Fidelity, google, open_pdf
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT
from squidpdf.core.coverage import Coverage
from squidpdf.core.fonts import FACES, face_bytes
from squidpdf.core.google import (
    Fetch,
    GoogleFile,
    blob_hash,
    family_list,
    fetched,
    google_fonts,
)
from tests.core.conftest import POPPINS, POPPINS_TEXT
from tests.helpers import assert_equal, assert_false, assert_true

_WANTED = "Yearly Hello"  # Y, a and y aren't in the file's copy of Poppins
_LACKED = ["Y", "a", "y"]
_DEADLINE_S = 0.1  # the whole-fetch deadline in the hang test
_HANG_S = 2.0  # how long its fake connection hangs: far past the deadline


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
        got = fetched(file, folder=tmp_path, download=_failing)
    assert_equal(got, None, "what a failed fetch hands back")
    assert_true("fetch failed" in caplog.text, "the failure was logged")


def test_bytes_the_pinned_commit_doesnt_have_are_not_used_or_kept(tmp_path):
    """A download is checked against git's hash for the file; only a match is kept."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    tampered = fetched(file, folder=tmp_path, download=_returning(b"not the font"))
    assert_equal(tampered, None, "what bytes of another hash give")
    assert_false(any(tmp_path.rglob("*.ttf")), "anything kept after a mismatch")

    got = fetched(file, folder=tmp_path, download=_returning(poppins))
    assert_equal(got, poppins, "what the pinned file's own bytes give")
    # Kept: the next ask is answered from disk, with the network down.
    assert_equal(fetched(file, folder=tmp_path, download=_failing), poppins, "from the cache")


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
        got = fetched(file, folder=tmp_path, download=_returning(poppins))

    assert_equal(got, poppins, "what the checked download gives")
    assert_true("not cached" in caplog.text, "the failure was logged")
    assert_equal([path for path in tmp_path.rglob("*") if path.is_file()], [], "files left")


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


def test_a_fetch_that_hangs_gives_up_at_the_deadline(monkeypatch):
    """httpx's timeout is per step, so a hung connection is cut off by the whole deadline."""

    def hanging(*_args: object, **_kwargs: object) -> None:
        time.sleep(_HANG_S)

    monkeypatch.setattr(google, "FETCH_TIMEOUT_S", _DEADLINE_S)
    monkeypatch.setattr(google.httpx, "get", hanging)
    started = time.monotonic()
    with pytest.raises(Exception):  # noqa: B017 (whatever it raises, analysis logs it)
        google.download("https://example.invalid/font.ttf")
    assert_true(time.monotonic() - started < _HANG_S, "gave up before the fetch did")
