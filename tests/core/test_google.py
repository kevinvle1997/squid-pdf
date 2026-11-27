from __future__ import annotations

import contextvars
import errno
import io
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import httpx
import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.TupleVariation import TupleVariation

from squidpdf.core import (
    FontSources,
    LineToDraw,
    constants,
    index_of,
    new_text,
    open_pdf,
    start_request,
    tally_fields,
)
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT
from squidpdf.core.fonts import google
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.embedded import FontUnusable
from squidpdf.core.fonts.google import (
    Fetch,
    blob_hash,
    google_fonts,
)
from squidpdf.core.fonts.google_files import GoogleFile, google_file
from squidpdf.core.types import FontDescriptor
from tests.conftest import POPPINS, POPPINS_TEXT, name_two_byte_font
from tests.core.conftest import finished_font, square
from tests.helpers import (
    assert_at_most,
    assert_equal,
    assert_false,
    assert_in,
    assert_not_in,
    assert_true,
)

_WANTED = "Yearly Hello"
_LACKED = ["Y", "a", "y"]
_POPPINS_PATH = "ofl/poppins/Poppins-Regular.ttf"
_EM = 1000
_WIDE = 200
_DEADLINE_S = 0.1
_HANG_S = 2.0  # the longest its fake connection hangs: far past the deadline


# Aclonica has no italic, and Roboto, variable only, would be cut at any weight named.
_NOT_GOOGLES = [
    ("Arial", None, "google_not_listed"),
    ("Aclonica-Italic", None, "google_no_cut"),
    ("Assistant-Bold", None, "google_name_reserved"),
    ("Roboto", FontDescriptor(32, 450.0, 0.0, None), "google_no_cut"),
    ("Roboto", FontDescriptor(32, 0.0, 0.0, None), "google_no_cut"),
]


def _google(font_file: bytes | None) -> tuple[Fetch, list[GoogleFile]]:
    asked: list[GoogleFile] = []

    def fetch(file: GoogleFile) -> bytes | None:
        asked.append(file)
        return font_file

    return fetch, asked


def _missing(path: str, fetch: Fetch) -> list[str]:
    with open_pdf(path, sources=FontSources(google=fetch)) as eng:
        span = next(iter(eng.index()))
        return eng.fit.plan_for(span, _WANTED).missing


def _why_substitute(path: str, fetch: Fetch) -> str | None:
    with open_pdf(path, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        new = new_text(
            0,
            origin=(72, 200),
            text=_WANTED,
            size=span.size,
            font=span.font,
            font_object=span.font_object,
        )
        [report] = engine.assess(index_of([new]))
    return None if report.why is None else report.why.key


def _through(
    download: Callable[[str], bytes], folder: Path, *, cache_only: bool = False
) -> Fetch:
    fetch = google_fonts(folder=folder, cache_only=cache_only, download=download)
    if fetch is None:
        pytest.fail("no way to Google's copies, though a download was handed in")
    return fetch


def _fresh_fetch(
    file: GoogleFile, folder: Path, download: Callable[[str], bytes]
) -> bytes | None:
    return _through(download, folder)(file)


def _failing(url: str) -> bytes:
    raise OSError(f"no route to {url}")


def _not_found(url: str) -> bytes:
    raise httpx.HTTPStatusError(
        "404 Not Found", request=httpx.Request("GET", url), response=httpx.Response(404)
    )


def _returning(data: bytes) -> Callable[[str], bytes]:
    return lambda _url: data


def test_a_letter_no_copy_in_the_file_draws_comes_from_googles_copy_and_is_exact(
    poppins_subset, tmp_path
):
    fetch, asked = _google(POPPINS.read_bytes())
    out = str(tmp_path / "redrawn.pdf")
    with open_pdf(poppins_subset, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        missing = engine.fit.plan_for(span, _WANTED).missing
        [report] = engine.assess(engine.index())
        engine.remove([span], then_drawn=[LineToDraw(span, _WANTED)])
        engine.draw(span, _WANTED)
        engine.save(out)

    assert_equal((missing, report.state), ([], "exact"), "missing, and fidelity")
    assert_equal([file.path for file in asked], ["ofl/poppins/Poppins-Regular.ttf"], "fetched")
    saved = pymupdf.open(out)
    assert_equal(saved[0].get_text().strip(), _WANTED, "the text read back")
    font_files = [
        saved.extract_font(font_object)[-1] for font_object, *_ in saved[0].get_fonts()
    ]
    lending = [file for file in font_files if coverage_of(file).covers("Y")]
    assert_equal(len(lending), 1, "font files on the page that draw a Y")
    assert_at_most(len(lending[0]), len(POPPINS.read_bytes()) - 1, "Google's copy's size")


def test_googles_copy_lends_only_the_letters_it_draws(poppins_subset):
    fetch, _asked = _google(POPPINS.read_bytes())
    with open_pdf(poppins_subset, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        missing = engine.fit.plan_for(span, "YΩ").missing
        widths = engine.widths(span)

    assert_equal(
        (missing, "Ω" in widths, "Y" in widths),
        (["Ω"], False, True),
        "missing, and whether Ω and Y have a width",
    )


def test_a_google_copy_with_other_widths_lends_nothing(poppins_subset):
    fetch, _asked = _google(face_bytes(FACES["Liberation Sans Regular"]))
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")


def test_a_fetch_that_fails_leaves_the_line_to_the_substitute_and_is_logged(
    poppins_subset, tmp_path, caplog
):
    fetch, _asked = _google(None)
    assert_equal(_missing(poppins_subset, fetch), _LACKED, "letters Poppins lacks")

    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(POPPINS.read_bytes()), None)
    with caplog.at_level(logging.WARNING):
        got = _fresh_fetch(file, tmp_path, _failing)
    assert_equal(got, None, "what a failed fetch hands back")
    assert_in("google_fetch_failed", caplog.text, "the failure was logged")


def test_a_google_copy_that_cant_be_had_is_named_in_the_fonts_why(poppins_subset):
    not_fetched, _asked = _google(None)
    unreadable, _asked = _google(b"not a font")

    whys = [_why_substitute(poppins_subset, fetch) for fetch in (not_fetched, unreadable)]

    assert_equal(whys, ["google_not_fetched", "google_unreadable"], "why a similar font draws")


def test_a_google_copy_found_unusable_is_remembered_by_its_reason_alone(poppins_subset):
    unreadable, asked = _google(b"not a font")
    with open_pdf(poppins_subset, sources=FontSources(google=unreadable)) as engine:
        span = next(iter(engine.index()))
        new = new_text(
            0,
            origin=(72, 200),
            text=_WANTED,
            size=span.size,
            font=span.font,
            font_object=span.font_object,
        )
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
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    tampered = _fresh_fetch(file, tmp_path, _returning(b"not the font"))
    assert_equal(tampered, None, "what bytes of another hash give")
    assert_false(any(tmp_path.rglob("*.ttf")), "anything kept after a mismatch")

    got = _fresh_fetch(file, tmp_path, _returning(poppins))
    assert_equal(got, poppins, "what the pinned file's own bytes give")
    assert_equal(_fresh_fetch(file, tmp_path, _failing), poppins, "from the cache")


def test_a_cache_miss_is_logged_once_and_a_hit_logs_nothing(tmp_path, caplog):
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)

    with caplog.at_level(logging.INFO, logger=google.__name__):
        _through(_failing, tmp_path, cache_only=True)(file)
        _fresh_fetch(file, tmp_path, _returning(poppins))
    lines = [record.getMessage() for record in caplog.records]
    misses = [line for line in lines if "google_cache_missed" in line]
    miss = f"skipped core.fonts.google google_cache_missed font={file.source}"
    assert_equal(
        misses,
        [f"{miss} may_download=false", f"{miss} may_download=true"],
        "one line per miss, render's then analysis's",
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=google.__name__):
        _fresh_fetch(file, tmp_path, _failing)
    assert_equal(caplog.records, [], "what a hit logs")


def test_a_request_tallies_what_googles_copies_cost_it(tmp_path):
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    answering = _through(_returning(poppins), tmp_path)
    down = _through(_failing, tmp_path / "down")

    def tallied_by(fetch: Fetch) -> list[str]:
        start_request()
        fetch(file)
        return sorted(tally_fields())

    said = [
        contextvars.copy_context().run(tallied_by, fetch)
        for fetch in [answering, answering, down, down]
    ]

    expected = [
        ["google_fetched", "google_ms"],
        ["google_hit"],
        ["google_failed", "google_ms"],
        ["google_failed"],
    ]
    assert_equal(said, expected, "a download, the cache, a failure, then one held back")


def test_a_cache_that_cant_be_written_still_lends_the_copy_and_leaves_no_piece(
    tmp_path, monkeypatch, caplog
):
    poppins = POPPINS.read_bytes()
    file = GoogleFile("ofl/poppins/Poppins-Regular.ttf", blob_hash(poppins), None)

    def disk_full(*_args: object) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(os, "replace", disk_full)
    with caplog.at_level(logging.WARNING):
        got = _fresh_fetch(file, tmp_path, _returning(poppins))

    assert_true(got == poppins, "the checked download was handed back")
    assert_in("google_not_cached", caplog.text, "the failure was logged")
    assert_equal([path for path in tmp_path.rglob("*") if path.is_file()], [], "files left")


def test_a_damaged_cached_copy_is_deleted_logged_and_fetched_again(tmp_path, caplog):
    poppins = POPPINS.read_bytes()
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"")

    with caplog.at_level(logging.INFO, logger=google.__name__):
        got = _fresh_fetch(file, tmp_path, _returning(poppins))

    assert_true(got == poppins, "Google's copy was handed back")
    assert_in("damaged", caplog.text, "the bad copy was logged")
    assert_not_in("google_cache_missed", caplog.text, "a damaged copy logged as a miss too")
    assert_true(cached.read_bytes() == poppins, "the cache holds Google's copy after")


def test_a_damaged_copy_on_a_read_only_disk_is_left_and_fetched_again(tmp_path, monkeypatch):
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


def _variable_font() -> bytes:
    builder = FontBuilder(_EM, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({ord("A"): "A"})
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": square()})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 50)})
    return finished_font(builder, family="Madeup", before_saving=_bolder_is_wider)


def _bolder_is_wider(builder: FontBuilder) -> None:
    builder.setupFvar(axes=[("wght", 100, 400, 900, "Weight")], instances=[])
    moved = [(0, 0), (0, 0), (_WIDE, 0), (_WIDE, 0), (0, 0), (_WIDE, 0), (0, 0), (0, 0)]
    builder.setupGvar({"A": [TupleVariation({"wght": (0, 1, 1)}, moved)]})


def test_a_variable_font_is_cut_to_the_weight_and_the_cut_is_checked_when_read(tmp_path):
    variable = _variable_font()
    file = GoogleFile("ofl/madeup/Madeup[wght].ttf", blob_hash(variable), 700)

    got = _fresh_fetch(file, tmp_path, _returning(variable))
    if got is None:
        pytest.fail("no copy was had")
    cut = TTFont(io.BytesIO(got))
    assert_not_in("fvar", cut, "the cut still varies")
    assert_equal(cut["hmtx"]["A"][0], 620, "A's width at weight 700")
    again = _fresh_fetch(file, tmp_path, _failing)
    assert_true(again == got, "the cut is read back from the cache")

    (tmp_path / GOOGLE_FONTS_COMMIT / file.source).write_bytes(got[:-1])
    assert_equal(_fresh_fetch(file, tmp_path, _failing), None, "a cut gone bad")


def test_two_cuts_of_one_file_are_the_same_bytes_whenever_they_are_made(tmp_path, monkeypatch):
    variable = _variable_font()
    file = GoogleFile("ofl/madeup/Madeup[wght].ttf", blob_hash(variable), 700)
    # fontTools stamps a saved font with the time, read from here when it's set.
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    first = _fresh_fetch(file, tmp_path / "first", _returning(variable))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "2000000000")
    later = _fresh_fetch(file, tmp_path / "later", _returning(variable))
    if first is None:
        pytest.fail("no copy was had")
    assert_true(later == first, "a later cut matches the first")


def test_a_font_google_doesnt_have_is_never_fetched(pdf):
    fetch, asked = _google(POPPINS.read_bytes())
    with open_pdf(pdf, sources=FontSources(google=fetch)) as engine:
        for span in engine.index():
            engine.fit.plan_for(span, "Ωxyzq")
    assert_equal(asked, [], "files fetched")
    for font, descriptor, why in _NOT_GOOGLES:
        with pytest.raises(FontUnusable) as raised:
            google_file(font, descriptor)
        assert_equal(raised.value.reason.key, why, f"why Google has no file for {font}")


def _described_as(path: str, out: Path, *, name: str, described: dict[str, str]) -> str:
    doc = pymupdf.open(path)
    [(font_object, *_)] = doc[0].get_fonts()
    # A space in a PDF name is written #20.
    inner = name_two_byte_font(doc, font_object, name.replace(" ", "#20"))
    _kind, descriptor_reference = doc.xref_get_key(inner, "FontDescriptor")
    descriptor = int(descriptor_reference.split()[0])
    for key, value in described.items():
        doc.xref_set_key(descriptor, key, value)
    doc.save(out)
    return str(out)


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
    path = _described_as(
        poppins_subset, tmp_path / "described.pdf", name=name, described=described
    )
    fetch, asked = _google(None)
    with open_pdf(path, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        drawn_in = engine.fit.substitute(span, _WANTED, plan=engine.fit.plan_for(span, _WANTED))
    asked_for = [Path(file.path).name for file in asked]
    said = _why_substitute(path, fetch)

    expected = (
        ([], face, "google_no_cut")
        if file_name is None
        else ([file_name], face, "google_not_fetched")
    )
    assert_equal((asked_for, drawn_in, said), expected, "the file asked for, the face, why")


def test_the_poppins_fixture_is_google_s_file_and_draws_only_its_line():
    listed = "0bda228ade88b0bb5aac7da2c881d0c3f64d0817"
    assert_equal(blob_hash(POPPINS.read_bytes()), listed, "the test font's git hash")
    assert_false(any(ch in POPPINS_TEXT for ch in _LACKED), "the line uses a lacked letter")


def test_the_vendored_family_list_was_read_from_the_pinned_commit():
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    commit = json.loads(listed.read_text())["commit"]
    assert_equal(commit, GOOGLE_FONTS_COMMIT, "the list's commit")


def test_nothing_is_fetched_when_told_not_to():
    assert_equal(google_fonts(), None, "the way to Google's copies")


def test_a_failed_fetch_is_not_tried_again_for_a_while(tmp_path):
    asked: list[str] = []

    def failing(url: str) -> bytes:
        asked.append(url)
        return _failing(url)

    file = GoogleFile(_POPPINS_PATH, blob_hash(POPPINS.read_bytes()), None)
    fetch = _through(failing, tmp_path)
    for _ in range(3):
        fetch(file)
    assert_equal(len(asked), 1, "downloads tried")


@pytest.mark.parametrize(
    ("download", "others_tried"),
    [(_failing, 0), (_not_found, 1)],
    ids=["no answer", "an answer without the file"],
)
def test_a_failed_download_holds_back_every_file_only_when_nothing_answered(
    tmp_path, download, others_tried
):
    poppins = POPPINS.read_bytes()
    asked: list[str] = []

    def answering(url: str) -> bytes:
        asked.append(url)
        return poppins

    file = GoogleFile("ofl/poppins/Poppins-Bold.ttf", "a hash never asked for", None)
    other = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)

    def by_file(url: str) -> bytes:
        return download(url) if url == google.raw_url(file.path) else answering(url)

    fetch = _through(by_file, tmp_path)
    fetch(file)
    fetch(other)
    assert_equal(len(asked), others_tried, "downloads of another file tried")


def test_a_fetch_answered_past_its_deadline_keeps_the_answer_and_lifts_the_hold(
    tmp_path, monkeypatch
):
    poppins = POPPINS.read_bytes()
    released = threading.Event()
    asked: list[str] = []

    def hanging(url: str) -> bytes:
        asked.append(url)
        released.wait(_HANG_S)
        return poppins

    monkeypatch.setattr(constants, "FETCH_TIMEOUT_S", _DEADLINE_S)
    file = GoogleFile(_POPPINS_PATH, blob_hash(poppins), None)
    other = GoogleFile("ofl/poppins/Poppins-Bold.ttf", "a hash never asked for", None)
    fetch = _through(hanging, tmp_path)
    started = time.monotonic()
    got = fetch(file)
    assert_at_most(time.monotonic() - started, _HANG_S / 2, "seconds waited")
    assert_equal(got, None, "what came by the deadline")

    released.set()
    cached = tmp_path / GOOGLE_FONTS_COMMIT / file.source
    deadline = time.monotonic() + _HANG_S
    while not cached.exists() and time.monotonic() < deadline:
        time.sleep(_DEADLINE_S)
    late = fetch(file)
    assert_true(late == poppins, "the late answer was kept, and is read from the cache")
    fetch(other)
    assert_equal(len(asked), 2, "downloads tried, once the network answered")
