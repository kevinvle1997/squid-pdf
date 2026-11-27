"""Only a font's path leaves the server."""

from __future__ import annotations

import hashlib
import io
import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

import squidpdf.core.constants as constants
from squidpdf.core.app.logs import LogController, LogEvent, OwnText, Tally, ms_since, tally
from squidpdf.core.app.message import Message
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable, recalled
from squidpdf.core.fonts.google_files import GoogleFile, google_file
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.fonts.pool import FontCopy, Lent, lent_widths
from squidpdf.core.pdf.driver import DriverError, PdfDriver
from squidpdf.core.types import PageFont

_log = LogController.for_module(__name__)

_RAW = "https://raw.githubusercontent.com/google/fonts"
# Set to anything: GitHub is never asked. For tests, and a server with no way out.
_NO_FETCH = "SQUIDPDF_NO_FETCH"
_WIDTH = 100
_HASH_SUFFIX = ".sha1"
_EVERY_FILE = "*"  # in a _RetryRecord: a download got no answer, so the network is down


type GoogleDownload = Callable[[str], bytes]
type Fetch = Callable[[GoogleFile], bytes | None]


@dataclass(slots=True)
class _RetryRecord:
    """Each process keeps its own: each worker learns on its own."""

    retry_at: dict[str, float] = field(default_factory=dict)
    # So a hold can't miss an answer, then hold back every file after that answer lifted it.
    lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def holds(self, source: str, now: float) -> bool:
        with self.lock:
            held_until = max(
                self.retry_at.get(source, 0.0), self.retry_at.get(_EVERY_FILE, 0.0)
            )
        return now < held_until

    def hold(self, source: str, until: float, *, answered: threading.Event) -> None:
        """No answer means the network failed, not the file: every file would wait as long."""
        with self.lock:
            held = source if answered.is_set() else _EVERY_FILE
            self.retry_at[held] = until

    def note_answer(self, answered: threading.Event) -> None:
        with self.lock:
            answered.set()
            self.retry_at.pop(_EVERY_FILE, None)

    def forget(self) -> None:
        with self.lock:
            self.retry_at.clear()


_retry_record = _RetryRecord()


@dataclass(frozen=True, slots=True, eq=False)
class GoogleFontController:
    driver: PdfDriver
    fetch: Fetch
    fonts: dict[str, EmbeddedFont | FontUnusable] = field(default_factory=dict, repr=False)

    def copy_of(self, own: FontCopy) -> FontCopy:
        file = self._file_for(own.font)
        embedded = recalled(self.fonts, file.source, partial(self._open, file))
        lent = Lent(file.source, strip_subset(own.font.name))
        return FontCopy(own.font, embedded, lent_widths(embedded), lent)

    def _file_for(self, font: PageFont) -> GoogleFile:
        return google_file(font.name, self.driver.font_descriptor(font.font_object))

    def _open(self, file: GoogleFile) -> EmbeddedFont:
        font_file = self.fetch(file)
        # None, or empty: MuPDF would quietly open a font of its own for no bytes.
        if not font_file:
            raise FontUnusable(Message("google_not_fetched"))
        try:
            program = self.driver.open_font(font_file)
        except DriverError as problem:
            raise FontUnusable(Message("google_unreadable")) from problem
        return EmbeddedFont(program, font_file, None)


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def raw_url(path: str) -> str:
    return f"{_RAW}/{constants.GOOGLE_FONTS_COMMIT}/{path}"


def _fetched(
    file: GoogleFile, *, folder: Path, download: GoogleDownload | None, retries: _RetryRecord
) -> bytes | None:
    """A late one is still checked and cached; neither it nor a failure is waited on again."""
    cached_path = folder / constants.GOOGLE_FONTS_COMMIT / file.source
    cached_copy = _cached(file, cached_path, may_download=download is not None)
    if cached_copy is not None:
        tally(Tally.GOOGLE_HIT)
        return cached_copy
    # The cache alone: render and export lend what analysis fetched, and never wait.
    if download is None:
        return None
    now = time.monotonic()
    if retries.holds(file.source, now):
        tally(Tally.GOOGLE_FAILED)
        return None
    fetching = _Fetching(cached_path=cached_path, download=download, retries=retries)
    font_file = _waited_for(file, fetching)
    if font_file is not None:
        tally(Tally.GOOGLE_FETCHED)
        return font_file
    tally(Tally.GOOGLE_FAILED)
    retries.hold(file.source, now + constants.FETCH_RETRY_S, answered=fetching.answered)
    return None


def _cached(file: GoogleFile, cached_path: Path, *, may_download: bool) -> bytes | None:
    try:
        return _read_cached_copy(cached_path, file)
    except FileNotFoundError:
        _log.write(
            LogEvent.GOOGLE_CACHE_MISSED,
            font=OwnText(file.source),
            may_download=may_download,
        )
        return None


def _waited_for(file: GoogleFile, fetching: _Fetching) -> bytes | None:
    # A daemon: one still hanging never keeps the worker from exiting.
    threading.Thread(target=_download_and_cache, args=(file, fetching), daemon=True).start()
    waited_from = time.monotonic()
    try:
        font_file = fetching.answer.get(timeout=constants.FETCH_TIMEOUT_S)
    except queue.Empty:
        _log.write(
            LogEvent.GOOGLE_FETCH_LATE,
            font=OwnText(file.path),
            timeout_s=constants.FETCH_TIMEOUT_S,
        )
        font_file = None
    tally(Tally.GOOGLE_MS, ms_since(waited_from))
    return font_file


@dataclass(frozen=True, slots=True, eq=False)
class _Fetching:
    cached_path: Path
    download: GoogleDownload
    retries: _RetryRecord
    answer: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=1))
    answered: threading.Event = field(default_factory=threading.Event)


def _download_and_cache(file: GoogleFile, fetching: _Fetching) -> None:
    font_file: bytes | None = None
    try:
        font_file = _download_checked_and_cut(file, fetching)
        if font_file is not None:
            _cache_copy(fetching.cached_path, file, font_file)
    finally:
        fetching.answer.put(font_file)


def _cache_copy(cached_path: Path, file: GoogleFile, font_file: bytes) -> None:
    _kept(cached_path, font_file)
    if file.weight is None:
        return
    # A cut isn't the file git hashed, so it's checked by its own.
    _kept(_hash_beside(cached_path), blob_hash(font_file).encode())


def _download_checked_and_cut(file: GoogleFile, fetching: _Fetching) -> bytes | None:
    try:
        whole = fetching.download(raw_url(file.path))
    except httpx.HTTPStatusError as missing:
        _log.write(LogEvent.GOOGLE_NOT_ON_GITHUB, missing, font=OwnText(file.path))
        fetching.retries.note_answer(fetching.answered)
        return None
    except Exception as no_answer:  # noqa: BLE001 (no answer: a network fails in many ways)
        _log.write(LogEvent.GOOGLE_FETCH_FAILED, no_answer, font=OwnText(file.path))
        return None
    # The network works after all: a wait that ran out held back every file for nothing.
    fetching.retries.note_answer(fetching.answered)
    if blob_hash(whole) != file.blob:
        _log.write(LogEvent.GOOGLE_WRONG_FILE, font=OwnText(file.path))
        return None
    try:
        return whole if file.weight is None else _cut(whole, file.weight)
    except Exception as uncut:  # noqa: BLE001 (fontTools fails in many ways on a font)
        _log.write(LogEvent.GOOGLE_CUT_FAILED, uncut, font=OwnText(file.source))
        return None


def _read_cached_copy(cached_path: Path, file: GoogleFile) -> bytes | None:
    """Checked on every read: a copy cut short by a crash would be trusted for good."""
    font_file = cached_path.read_bytes()
    expected_hash = file.blob if file.weight is None else _hash_beside(cached_path).read_text()
    if blob_hash(font_file) == expected_hash:
        return font_file
    _log.write(LogEvent.GOOGLE_CACHE_DAMAGED, font=OwnText(file.source))
    try:
        cached_path.unlink(missing_ok=True)
    except OSError as kept:
        _log.write(LogEvent.GOOGLE_DAMAGED_KEPT, kept, font=OwnText(file.source))
    return None


def _hash_beside(cached_path: Path) -> Path:
    return cached_path.with_name(f"{cached_path.name}{_HASH_SUFFIX}")


def _cut(variable_font: bytes, weight: int) -> bytes:
    """MuPDF draws a variable font only at its default weight."""
    font = TTFont(io.BytesIO(variable_font), recalcTimestamp=False)
    axes = {axis.axisTag: axis for axis in font["fvar"].axes}
    wanted = {"wght": weight, "wdth": _WIDTH}
    location = {
        tag: min(max(wanted.get(tag, axis.defaultValue), axis.minValue), axis.maxValue)
        for tag, axis in axes.items()
    }
    fixed = instantiateVariableFont(font, location)
    out = io.BytesIO()
    fixed.save(out)
    return out.getvalue()


def _kept(path: Path, font_file: bytes) -> None:
    """Written beside it, then renamed over it, so a reader never sees half."""
    part: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as partial_file:
            part = Path(partial_file.name)
            partial_file.write(font_file)
        os.replace(part, path)
    except OSError as unwritten:
        _log.write(LogEvent.GOOGLE_NOT_CACHED, unwritten, font=OwnText(path.name))
        if part is not None:
            part.unlink(missing_ok=True)


def _download(url: str) -> bytes:
    """httpx's timeout is per step, not for the whole fetch: `_fetched` keeps that deadline."""
    response = httpx.get(url, timeout=constants.FETCH_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _cache_folder() -> Path:
    fonts_folder = os.environ.get("SQUIDPDF_FONTS")
    if fonts_folder:
        return Path(fonts_folder).resolve()
    user_caches = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(user_caches, "squidpdf", "fonts").resolve()


def google_fonts(
    *,
    folder: Path | None = None,
    cache_only: bool = False,
    download: GoogleDownload | None = None,
) -> Fetch | None:
    if download is None and os.environ.get(_NO_FETCH):
        return None
    cache = _cache_folder() if folder is None else folder
    # The process's record is GitHub's: a stand-in's failures say nothing of it.
    retries = _retry_record if download is None else _RetryRecord()
    way_out = _download if download is None else download
    return partial(
        _fetched, folder=cache, download=None if cache_only else way_out, retries=retries
    )
