"""Google's copy of a font: fetched by name, cached soundly, lent if its widths agree."""

from __future__ import annotations

import errno
import io
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from functools import partial
from importlib import resources
from pathlib import Path

import httpx
import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from squidpdf.core import FontSources, LineToDraw, index_of, new_text, open_pdf
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT
from squidpdf.core.fonts import google
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.embedded import FontUnusable
from squidpdf.core.fonts.google import (
    Fetch,
    GoogleFile,
    _fetched,  # noqa: PLC2701 (fetches with a stand-in network: google_fonts reaches the real one)
    _google_file,  # noqa: PLC2701 (why Google has no file, per font: no sample uses these)
    _KeptWidths,  # noqa: PLC2701 (the bound on a holder kept for a worker's life)
    _RetryRecord,  # noqa: PLC2701 (fetches with a stand-in network: google_fonts reaches the real one)
    blob_hash,
    google_fonts,
)
from squidpdf.core.types import FontDescriptor
from tests.conftest import name_two_byte_font
from tests.core.conftest import POPPINS, POPPINS_TEXT
from tests.helpers import assert_at_most, assert_equal, assert_false, assert_true

_WANTED = "Yearly Hello"  # Y, a and y aren't in the file's copy of Poppins
_LACKED = ["Y", "a", "y"]
_POPPINS_PATH = "ofl/poppins/Poppins-Regular.ttf"
_EM = 1000
_WIDE = 200  # how much wider the made-up variable font's A is at its heaviest
_DEADLINE_S = 0.1  # the whole-fetch deadline in the hang test
_HANG_S = 2.0  # the longest its fake connection hangs: far past the deadline
_COPIES = 100  # Google copies measured in one worker: far past how many it keeps


# A font, its description, and why Google has no file for it: Aclonica has no italic,
# and Roboto, variable only, would be cut at any weight named, even 450 or 0.
_NOT_GOOGLES = [
    ("Arial", None, "google_not_listed"),
    ("Aclonica-Italic", None, "google_no_cut"),
    ("Assistant-Bold", None, "google_name_reserved"),
    ("Roboto", FontDescriptor(32, 450.0, 0.0), "google_no_cut"),
    ("Roboto", FontDescriptor(32, 0.0, 0.0), "google_no_cut"),
]


def _google(font_file: bytes | None) -> tuple[Fetch, list[GoogleFile]]:
    """A fetch handing back `font_file` for anything asked, and the list of what was asked."""
    asked: list[GoogleFile] = []

    def fetch(file: GoogleFile) -> bytes | None:
        asked.append(file)
        return font_file

    return fetch, asked


def _missing(path: str, fetch: Fetch) -> list[str]:
    """What the fit says the first span can't draw of _WANTED, with `fetch` to Google."""
    with open_pdf(path, sources=FontSources(google=fetch)) as eng:
        span = next(iter(eng.index()))
        return eng.plan_for(span, _WANTED).missing


def _why_substitute(path: str, fetch: Fetch) -> str | None:
    """Why new text reading _WANTED in the first span's font is a substitute, by its key."""
    with open_pdf(path, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        new = new_text(0, origin=(72, 200), text=_WANTED, size=span.size, font=span.font)
        [report] = engine.assess(index_of([new]))
    return None if report.why is None else report.why.key


def _fresh_fetch(
    file: GoogleFile, folder: Path, download: Callable[[str], bytes]
) -> bytes | None:
    """`_fetched`, remembering no failure from any call before."""
    return _fetched(file, folder=folder, download=download, retries=_RetryRecord())


def _failing(url: str) -> bytes:
    """A download that never gets through, as a network down does."""
    raise OSError(f"no route to {url}")


def _not_found(url: str) -> bytes:
    """A download GitHub answers, but without the file."""
    raise httpx.HTTPStatusError(
        "404 Not Found", request=httpx.Request("GET", url), response=httpx.Response(404)
    )


def _returning(data: bytes) -> Callable[[str], bytes]:
    """A download that hands back `data` whatever it's asked for."""
    return lambda _url: data


def test_a_letter_no_copy_in_the_file_draws_comes_from_googles_copy_and_is_exact(
    poppins_subset, tmp_path
):
    """The file's Poppins has no Y; Google's has, as wide: exact, and drawn from it."""
    fetch, asked = _google(POPPINS.read_bytes())
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(poppins_subset, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        missing = engine.plan_for(span, _WANTED).missing
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, _WANTED)])
        engine.draw(span, _WANTED)
        engine.save(out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    assert_equal([file.path for file in asked], ["ofl/poppins/Poppins-Regular.ttf"], "fetched")
    saved = pymupdf.open(out)
    assert_equal(saved[0].get_text().strip(), _WANTED, "the text read back")
    font_files = [saved.extract_font(xref)[-1] for xref, *_ in saved[0].get_fonts()]
    lending = [file for file in font_files if coverage_of(file).covers("Y")]
    assert_equal(len(lending), 1, "font files on the page that draw a Y")
    # Cut down to what was drawn, as a face we ship is: not the whole font.
    assert_true(len(lending[0]) < len(POPPINS.read_bytes()), "Google's copy was trimmed")


def test_a_google_copy_with_other_widths_lends_nothing(poppins_subset):
    """Liberation Sans handed back as Poppins: its letters are other widths, so none lend."""
    fetch, _asked = _google(face_bytes(FACES["Liberation Sans Regular"]))
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")


def test_a_fetch_that_fails_leaves_the_line_to_the_substitute_and_is_logged(
    poppins_subset, tmp_path, caplog
):
    """No Google copy to be had: the letters stay missing, and nothing is raised."""
    fetch, _asked = _google(None)
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")

    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(POPPINS.read_bytes()), None)
    with caplog.at_level(logging.WARNING):
        got = _fresh_fetch(file, tmp_path, _failing)
    assert_equal(got, None, "what a failed fetch hands back")
    assert_true("fetch failed" in caplog.text, "the failure was logged")


def test_a_google_copy_that_cant_be_had_is_named_in_the_fonts_why(poppins_subset):
    """The file's Poppins lacks Y: the reader hears why Google's copy didn't lend it either."""
    not_fetched, _asked = _google(None)
    unreadable, _asked = _google(b"not a font")

    whys = [_why_substitute(poppins_subset, fetch) for fetch in (not_fetched, unreadable)]

    assert_equal(whys, ["google_not_fetched", "google_unreadable"], "why a similar font draws")


def test_a_google_copy_found_unusable_is_remembered_by_its_reason_alone(poppins_subset):
    """Kept for the document's life: a traceback's frames would keep the bytes it was handed."""
    unreadable, asked = _google(b"not a font")
    with open_pdf(poppins_subset, sources=FontSources(google=unreadable)) as engine:
        span = next(iter(engine.index()))
        new = new_text(0, origin=(72, 200), text=_WANTED, size=span.size, font=span.font)
        engine.assess(index_of([new]))
        engine.assess(index_of([new]))
        google_copies = engine.fonts.google

    if google_copies is None:
        pytest.fail("the engine was handed a fetch but has no Google copies")
    [kept] = google_copies.fonts.values()
    if not isinstance(kept, FontUnusable):
        pytest.fail("bytes that aren't a font were opened")
    held = (kept.reason.key, kept.__traceback__, kept.__cause__, kept.__context__)
    assert_equal(
        held, ("google_unreadable", None, None, None), "the reason, and what else is kept"
    )
    assert_equal(len(asked), 1, "fetches, for the second ask answered as the first was")


def test_bytes_the_pinned_commit_doesnt_have_are_not_used_or_kept(tmp_path):
    """A download is checked against git's hash for the file; only a match is kept."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    tampered = _fresh_fetch(file, tmp_path, _returning(b"not the font"))
    assert_equal(tampered, None, "what bytes of another hash give")
    assert_false(any(tmp_path.rglob("*.ttf")), "anything kept after a mismatch")

    got = _fresh_fetch(file, tmp_path, _returning(poppins))
    assert_equal(got, poppins, "what the pinned file's own bytes give")
    # Kept: the next ask is answered from disk, with the network down.
    assert_equal(_fresh_fetch(file, tmp_path, _failing), poppins, "from the cache")


def test_a_cache_miss_is_logged_once_and_a_hit_logs_nothing(tmp_path, caplog):
    """One INFO line per miss, saying whether the call may download; counted by grep."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)

    with caplog.at_level(logging.INFO, logger=google.__name__):
        _fetched(file, folder=tmp_path, download=None, retries=_RetryRecord())
        _fresh_fetch(file, tmp_path, _returning(poppins))
    lines = [record.getMessage() for record in caplog.records]
    misses = [line for line in lines if "Google cache miss" in line]
    assert_equal(
        misses,
        [
            f"Google cache miss: {file.source} (cache only)",
            f"Google cache miss: {file.source} (may download)",
        ],
        "one line per miss, render's then analysis's",
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=google.__name__):
        _fresh_fetch(file, tmp_path, _failing)
    assert_equal(caplog.records, [], "what a hit logs")


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
        got = _fresh_fetch(file, tmp_path, _returning(poppins))

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

    with caplog.at_level(logging.INFO, logger=google.__name__):
        got = _fresh_fetch(file, tmp_path, _returning(poppins))

    assert_true(got == poppins, "Google's copy was handed back")
    assert_true("damaged" in caplog.text, "the bad copy was logged")
    assert_false("cache miss" in caplog.text, "a damaged copy logged as a miss too")
    assert_true(cached.read_bytes() == poppins, "the cache holds Google's copy after")


def test_a_damaged_copy_on_a_read_only_disk_is_left_and_fetched_again(tmp_path, monkeypatch):
    """A disk remounted read-only after errors: the bad copy can't go, and nothing raises."""
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"")

    def read_only(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    monkeypatch.setattr(Path, "unlink", read_only)
    got = _fresh_fetch(file, tmp_path, _returning(poppins))
    assert_true(got == poppins, "Google's copy was handed back")


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

    got = _fresh_fetch(file, tmp_path, _returning(variable))
    if got is None:
        pytest.fail("no copy was had")
    cut = TTFont(io.BytesIO(got))
    assert_false("fvar" in cut, "the cut still varies")
    assert_equal(cut["hmtx"]["A"][0], 620, "A's width at weight 700")
    again = _fresh_fetch(file, tmp_path, _failing)
    assert_true(again == got, "the cut is read back from the cache")

    (tmp_path / GOOGLE_FONTS_COMMIT / file.source).write_bytes(got[:-1])
    assert_equal(_fresh_fetch(file, tmp_path, _failing), None, "a cut gone bad")


def test_two_cuts_of_one_file_are_the_same_bytes_whenever_they_are_made(monkeypatch):
    """Two workers may cut one file at once, each keeping its hash: they must agree."""
    variable = _variable_font()
    # fontTools stamps a saved font with the time, read from here when it's set.
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    first = google._cut(variable, 700)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "2000000000")
    assert_true(google._cut(variable, 700) == first, "a later cut matches the first")


def test_a_slow_cut_holds_back_only_its_own_file(tmp_path, monkeypatch):
    """The network answered, so other files are still worth a try; the cut is kept when done."""
    variable = _variable_font()
    file = GoogleFile("ofl/madeup/Madeup[wght].ttf", blob_hash(variable), 700)
    poppins = POPPINS.read_bytes()
    other = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    released = threading.Event()
    cut = google._cut

    def slow_cut(variable_font: bytes, weight: int) -> bytes:
        released.wait(_HANG_S)
        return cut(variable_font, weight)

    monkeypatch.setattr(google, "FETCH_TIMEOUT_S", _DEADLINE_S)
    monkeypatch.setattr(google, "_cut", slow_cut)
    by_path = {google.raw_url(file.path): variable, google.raw_url(other.path): poppins}
    retries = _RetryRecord()
    try:
        late = _fetched(file, folder=tmp_path, download=by_path.__getitem__, retries=retries)
        got = _fetched(other, folder=tmp_path, download=by_path.__getitem__, retries=retries)
    finally:
        released.set()
    assert_equal(late, None, "the copy still being cut")
    assert_true(got == poppins, "another file was fetched")


def test_a_font_google_doesnt_have_is_never_fetched(pdf):
    """The sample's fonts aren't Google's: no fetch, whatever letters are missing."""
    fetch, asked = _google(POPPINS.read_bytes())
    with open_pdf(pdf, sources=FontSources(google=fetch)) as engine:
        for span in engine.index():
            engine.plan_for(span, "Ωxyzq")
    assert_equal(asked, [], "files fetched")
    for font, descriptor, why in _NOT_GOOGLES:
        with pytest.raises(FontUnusable) as raised:
            _google_file(font, descriptor)
        assert_equal(raised.value.reason.key, why, f"why Google has no file for {font}")


def _described_as(path: str, out: Path, *, name: str, described: dict[str, str]) -> str:
    """The Poppins fixture at `out`, its font named `name` and described by `described`."""
    doc = pymupdf.open(path)
    [(xref, *_)] = doc[0].get_fonts()
    # A space in a PDF name is written #20.
    inner = name_two_byte_font(doc, xref, name.replace(" ", "#20"))
    _kind, descriptor_reference = doc.xref_get_key(inner, "FontDescriptor")
    descriptor = int(descriptor_reference.split()[0])
    for key, value in described.items():
        doc.xref_set_key(descriptor, key, value)
    doc.save(out)
    return str(out)


# A font's name and description, the file Google is asked for (None: none), the substitute.
# Flags 33 is the fixture's 32 plus fixed width; 1 << 18 is ForceBold.
_WEIGHTS_SAID = [
    ("Poppins-Regular", {"FontWeight": "123456"}, None, "Poppins Bold"),
    ("Poppins-Regular", {"FontWeight": "-5"}, None, "Poppins Regular"),
    ("Poppins-Regular", {"FontWeight": "640"}, None, "Poppins Bold"),
    ("Poppins-Regular", {"Flags": str(32 | 1 << 18)}, "Poppins-Regular.ttf", "Poppins Bold"),
    ("Poppins-Medium", {"FontWeight": "700"}, "Poppins-Medium.ttf", "Poppins Regular"),
    ("Poppins-Light,Bold", {}, "Poppins-Light.ttf", "Poppins Bold"),
    ("Poppins Light Bold", {}, "Poppins-Light.ttf", "Poppins Bold"),
    ("Syncopate", {"FontWeight": "600"}, None, "Liberation Sans Bold"),
    ("AbhayaLibre-Black", {}, None, "Liberation Sans Bold"),
    ("Coustard-SemiBold", {}, None, "Liberation Sans Bold"),
    ("CourierPrime-Medium", {"Flags": "33"}, None, "Liberation Mono Regular"),
    ("Poppins-SemiLight", {}, None, "Poppins Regular"),
    ("FiraCode-Retina", {"Flags": "33"}, None, "Liberation Mono Regular"),
    ("IBMPlexMono-Text", {"Flags": "33"}, None, "IBM Plex Mono Regular"),
    ("IBMPlexMono-Medm", {"Flags": "33"}, "IBMPlexMono-Medium.ttf", "IBM Plex Mono Regular"),
    ("IBMPlexMono-SmBld", {"Flags": "33"}, "IBMPlexMono-SemiBold.ttf", "IBM Plex Mono Bold"),
    (
        "IBMPlexMono-ExtLt",
        {"Flags": "33"},
        "IBMPlexMono-ExtraLight.ttf",
        "IBM Plex Mono Regular",
    ),
]


@pytest.mark.parametrize(("name", "described", "file_name", "face"), _WEIGHTS_SAID)
def test_google_is_asked_only_for_the_font_s_own_weight_one_of_nine(
    poppins_subset, tmp_path, name, described, file_name, face
):
    """Only the font's own weight is asked for; the substitute is bold when the font is."""
    path = _described_as(
        poppins_subset, tmp_path / "described.pdf", name=name, described=described
    )
    fetch, asked = _google(None)
    with open_pdf(path, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        drawn_in = engine.substitute(span, _WANTED, plan=engine.plan_for(span, _WANTED))
    asked_for = [Path(file.path).name for file in asked]
    said = _why_substitute(path, fetch)

    # Asked for a file, the fetch hands back nothing; asked for none, Google has no cut.
    expected = (
        ([], face, "google_no_cut")
        if file_name is None
        else ([file_name], face, "google_not_fetched")
    )
    assert_equal((asked_for, drawn_in, said), expected, "the file asked for, the face, why")


def test_the_poppins_fixture_is_google_s_file_and_draws_only_its_line():
    """Guards the fixtures above: the test font is the pinned file, the trim keeps one line."""
    listed = "0bda228ade88b0bb5aac7da2c881d0c3f64d0817"  # Poppins-Regular.ttf at the pin
    assert_equal(blob_hash(POPPINS.read_bytes()), listed, "the test font's git hash")
    assert_false(any(ch in POPPINS_TEXT for ch in _LACKED), "the line uses a lacked letter")


def test_the_vendored_family_list_was_read_from_the_pinned_commit():
    """A new pin without a new list would fail every changed file's hash check, quietly."""
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    commit = json.loads(listed.read_text())["commit"]
    assert_equal(commit, GOOGLE_FONTS_COMMIT, "the list's commit")


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
    retries = _RetryRecord()
    for _ in range(3):
        _fetched(file, folder=tmp_path, download=failing, retries=retries)
    assert_equal(len(asked), 1, "downloads tried")


@pytest.mark.parametrize(
    ("download", "others_tried"),
    [(_failing, 0), (_not_found, 1)],
    ids=["no answer", "an answer without the file"],
)
def test_a_failed_download_holds_back_every_file_only_when_nothing_answered(
    tmp_path, download, others_tried
):
    """No route to GitHub: every other font would fail as fast. A 404 is that file's alone."""
    poppins = POPPINS.read_bytes()
    asked: list[str] = []

    def answering(url: str) -> bytes:
        asked.append(url)
        return poppins

    file = GoogleFile("ofl/poppins/Poppins-Bold.ttf", "a hash never asked for", None)
    other = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    retries = _RetryRecord()
    _fetched(file, folder=tmp_path, download=download, retries=retries)
    _fetched(other, folder=tmp_path, download=answering, retries=retries)
    assert_equal(len(asked), others_tried, "downloads of another file tried")


def test_a_fetch_with_no_answer_holds_back_every_file_until_an_answer_comes(
    tmp_path, monkeypatch
):
    """GitHub not answering: one wait, not one per font. A late answer is kept, and lifts it."""
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
    retries = _RetryRecord()
    started = time.monotonic()
    got = [
        _fetched(each, folder=tmp_path, download=hanging, retries=retries)
        for each in (file, other)
    ]
    assert_at_most(time.monotonic() - started, _HANG_S / 2, "seconds waited")
    assert_equal((got, len(asked)), ([None, None], 1), "what came, and downloads tried")

    released.set()
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    deadline = time.monotonic() + _HANG_S
    while not cached.exists() and time.monotonic() < deadline:
        time.sleep(_DEADLINE_S)
    late = _fetched(file, folder=tmp_path, download=_failing, retries=retries)
    assert_true(late == poppins, "the late answer was kept, and is read from the cache")
    _fetched(other, folder=tmp_path, download=hanging, retries=retries)
    assert_equal(len(asked), 2, "downloads tried, once the network answered")


def test_a_worker_keeps_the_widths_of_only_its_latest_google_copies():
    """A long-lived worker stays small: past its bound, the copy measured longest ago goes."""
    kept = _KeptWidths()
    measured: list[bytes] = []

    def measure(digest: bytes) -> dict[str, float]:
        measured.append(digest)
        return {"A": 600.0}

    digests = [n.to_bytes(2) for n in range(_COPIES)]
    for digest in digests:
        kept.widths(digest, partial(measure, digest))
    measured.clear()
    newest, oldest = digests[-1], digests[0]
    for digest in (newest, oldest):
        kept.widths(digest, partial(measure, digest))

    assert_equal(measured, [oldest], "copies measured again: the oldest, not the newest")
