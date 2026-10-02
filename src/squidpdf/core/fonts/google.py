"""Google's copy of a document's font: which file it is, and fetching it once per server.

Every family on Google Fonts is free to fetch from github.com/google/fonts. The
list of families, read from one pinned commit, ships as
`fonts/google-families.json` (rebuilt by `scripts/google_families.py`), so
matching a name needs no network. Only the file's path leaves the server.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, partial
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from squidpdf.core.app.message import Message
from squidpdf.core.constants import FETCH_RETRY_S, FETCH_TIMEOUT_S, GOOGLE_FONTS_COMMIT
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable, remembered
from squidpdf.core.fonts.look_alike import WEIGHTS, bare_name, style_of, weight_of
from squidpdf.core.pdf.driver import DriverError, PdfDriver
from squidpdf.core.types import FontDescriptor, PageFont

_logger = logging.getLogger(__name__)

_RAW = "https://raw.githubusercontent.com/google/fonts"
# Set to anything: never fetch. For tests, and a server with no way out.
_NO_FETCH = "SQUIDPDF_NO_FETCH"
_WIDTH = 100  # a variable font's usual width, as `fonts/README.md` cuts ours
_HASH_SUFFIX = ".sha1"  # a cut copy's own hash, in a file beside it
_EVERY_FILE = "*"  # in a _RetryRecord: a download got no answer, so the network is down
# One INFO line per cache miss, worded the same every time so a grep counts them.
_CACHE_MISS = "Google cache miss: %s (%s)"


@dataclass(frozen=True, slots=True)
class _ListedFile:
    """One file of a family, as the vendored list has it."""

    name: str  # e.g. "Poppins-Bold.ttf", or "OpenSans[wdth,wght].ttf"
    weight: int  # 100 to 900; a variable file's default
    italic: bool
    variable: bool  # one file for every weight, to be cut to one
    blob: str  # git's hash of it at the pinned commit


@dataclass(frozen=True, slots=True)
class GoogleFile:
    """One font file in Google's collection at the pinned commit, and how to draw with it."""

    path: str  # under the commit, e.g. "ofl/poppins/Poppins-Bold.ttf"
    blob: str  # git's hash of it at that commit, to check a download by
    weight: int | None  # for a variable font, the weight to cut it at; None when fixed

    @property
    def source(self) -> str:
        """What it's cached and added to a page as: the path, and the weight a cut is at."""
        return self.path if self.weight is None else f"{self.path}@{self.weight}"


# The bytes at a URL; raises when it can't get them, httpx's HTTPStatusError when the
# server answered without them. Tests hand in one reading a local file.
type _Download = Callable[[str], bytes]
# The engine's way to Google's copy: its bytes, or None when there's none to be had.
type Fetch = Callable[[GoogleFile], bytes | None]


@dataclass(slots=True)
class _RetryRecord:
    """What failed to come, and when each may be tried again, as `time.monotonic()` reads.

    By `GoogleFile.source`; `_EVERY_FILE` holds back all of them. Each process
    keeps its own (`_retry_record`): each worker learns on its own.
    """

    retry_at: dict[str, float] = field(default_factory=dict)
    # Taken by every read and change. A download's thread notes its answer while the
    # one waiting on it may be holding back every file: one wholly before the other.
    lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def holds(self, source: str, now: float) -> bool:
        """Whether `source` failed a moment ago, or the network did: not worth a wait yet."""
        with self.lock:
            # .get: most files, and the network, have never failed
            held_until = max(
                self.retry_at.get(source, 0.0), self.retry_at.get(_EVERY_FILE, 0.0)
            )
        return now < held_until

    def hold(self, source: str, until: float, *, answered: threading.Event) -> None:
        """Try `source` again only from `until` on; every file, if the network never `answered`.

        No answer means the network failed, not the file, and every file would
        fail or wait as long.
        """
        with self.lock:
            held = source if answered.is_set() else _EVERY_FILE
            self.retry_at[held] = until

    def note_answer(self, answered: threading.Event) -> None:
        """The network answered: set `answered`, and lift any hold on every file."""
        with self.lock:
            answered.set()
            self.retry_at.pop(_EVERY_FILE, None)  # None: most answers find no hold to lift

    def forget(self) -> None:
        """Forget every failure, as a fresh record."""
        with self.lock:
            self.retry_at.clear()


# This process's record of what failed to come.
_retry_record = _RetryRecord()


@dataclass(frozen=True, slots=True, eq=False)
class GoogleFontController:
    """Google's copy of a document's fonts: matched, fetched and opened, once per file."""

    driver: PdfDriver  # the document's, whose fonts are matched
    fetch: Fetch  # how each file Google has is got
    # Each file, opened, or why it can't be had, by `GoogleFile.source`.
    fonts: dict[str, EmbeddedFont | FontUnusable] = field(default_factory=dict, repr=False)

    def file_for(self, font: PageFont) -> GoogleFile:
        """Google's file for one of the document's fonts. Raises FontUnusable when none fits."""
        return _google_file(font.name, self.driver.font_descriptor(font.xref))

    def opened(self, file: GoogleFile) -> EmbeddedFont | FontUnusable:
        """`file`, fetched and opened once, or why it can't be had or opened."""
        return remembered(self.fonts, file.source, partial(self._open, file))

    def _open(self, file: GoogleFile) -> EmbeddedFont:
        """`file`, fetched and opened. Raises FontUnusable when it can't be had or opened."""
        font_file = self.fetch(file)
        # None, or empty: MuPDF would quietly open a font of its own for no bytes.
        if not font_file:
            raise FontUnusable(Message("google_not_fetched"))
        try:
            program = self.driver.open_font(font_file)
        except DriverError as problem:  # the library can't read it, though git vouched for it
            raise FontUnusable(Message("google_unreadable")) from problem
        return EmbeddedFont(program, font_file, coverage_of(font_file), None)


@cache
def _family_list() -> dict[str, Any]:
    """The vendored family list, read once per process."""
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    return json.loads(listed.read_text())


def _google_file(font: str, descriptor: FontDescriptor | None) -> GoogleFile:
    """The file in Google's collection for a document's font.

    Matched by family name, then by the font's own weight and style, exactly:
    a file of another weight is no copy of it, though a fixed-width one would
    pass the width check. Only the nine usual weights are asked for, so a file
    naming a new weight on each page can't ask for a cut of each. A family that
    ships only a variable font is cut to the weight, unless its licence
    reserves its name: a cut is a modified version, which may not carry a
    reserved name. Raises FontUnusable, saying why, when there's none to use.
    """
    listed = _family_list()
    key = bare_name(font)
    key = listed["aliases"].get(key, key)  # .get: most families were never renamed
    family = listed["families"].get(key)  # .get: most fonts aren't Google's
    # Not one of Google's families: Arial, Calibri, a TeX font.
    if family is None:
        raise FontUnusable(Message("google_not_listed"))
    weight = weight_of(font, descriptor)
    # None of the nine, as a Text cut's 450 or a 0 is: no fixed file is in it, and a
    # variable one would be cut at any weight the file names.
    if weight not in WEIGHTS:
        raise FontUnusable(Message("google_no_cut"))
    style, _usual_cut = style_of(font, descriptor)
    italic = style in ("italic", "bold-italic")
    files = [_ListedFile(*row) for row in family["files"]]  # rows keep the fields' order
    same_slant = [file for file in files if file.italic == italic]
    folder = family["folder"]
    # A fixed file in this weight and slant.
    fixed = next((f for f in same_slant if not f.variable and f.weight == weight), None)
    if fixed is not None:
        return GoogleFile(f"{folder}/{fixed.name}", fixed.blob, None)
    variable = next((file for file in same_slant if file.variable), None)
    # Neither: no file in this weight and slant.
    if variable is None:
        raise FontUnusable(Message("google_no_cut"))
    # Variable only, but its licence reserves its name, which a cut may not carry.
    if family["reserved_name"]:
        raise FontUnusable(Message("google_name_reserved"))
    return GoogleFile(f"{folder}/{variable.name}", variable.blob, weight)


def blob_hash(data: bytes) -> str:
    """The hash git files `data` under: sha1 of `blob <length>`, a zero byte, then `data`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def raw_url(path: str) -> str:
    """Where GitHub serves `path` in Google's collection, as it was at the pinned commit."""
    return f"{_RAW}/{GOOGLE_FONTS_COMMIT}/{path}"


def _fetched(
    file: GoogleFile, *, folder: Path, download: _Download | None, retries: _RetryRecord
) -> bytes | None:
    """Google's copy of `file`, ready to draw with; None when there's none to be had.

    From the cache in `folder` when it's there and sound. Else, given `download`,
    downloaded in the background and waited on for FETCH_TIMEOUT_S at most; one
    that finishes later is still checked, cut and cached, for the next analysis.
    A failure is logged, not raised, and noted in `retries`, so nothing waits
    on it again for FETCH_RETRY_S. A download that gets no answer, by its
    deadline or at all, holds back every file.
    """
    cached_path = folder / GOOGLE_FONTS_COMMIT / file.source
    try:
        cached_copy = _read_cached_copy(cached_path, file)
    except FileNotFoundError:  # not cached yet, or a cut whose hash was never written
        # Counted by grep: how often the cache is empty, and whether this call may download.
        next_step = "cache only" if download is None else "may download"
        _logger.info(_CACHE_MISS, file.source, next_step)
        cached_copy = None
    # In the cache and sound: nothing to download. A damaged copy is logged where it's read.
    if cached_copy is not None:
        return cached_copy
    # The cache alone: render and export lend what analysis fetched, and never wait.
    if download is None:
        return None
    now = time.monotonic()
    # Failed a moment ago: not worth another wait yet.
    if retries.holds(file.source, now):
        return None
    fetching = _Fetching(cached_path=cached_path, download=download, retries=retries)
    # A daemon: one still hanging never keeps the worker from exiting.
    threading.Thread(target=_download_and_cache, args=(file, fetching), daemon=True).start()
    try:
        font_file = fetching.answer.get(timeout=FETCH_TIMEOUT_S)
    except queue.Empty:  # not ready by the deadline: it carries on, and caches what it gets
        _logger.warning("No Google copy of %s: not ready in %s s", file.path, FETCH_TIMEOUT_S)
        font_file = None
    # Had: nothing to hold back.
    if font_file is not None:
        return font_file
    # None came, or nothing yet: every file waits if the network never answered; else,
    # a file GitHub hasn't or a slow cut, only this one.
    retries.hold(file.source, now + FETCH_RETRY_S, answered=fetching.answered)
    return None


@dataclass(frozen=True, slots=True, eq=False)
class _Fetching:
    """One download under way in the background, and what it tells the one waiting on it."""

    cached_path: Path  # where it's cached
    download: _Download
    retries: _RetryRecord  # the record of failures, lifted for every file once an answer comes
    # The copy, ready to draw with, or None; put once, whether or not anyone still waits.
    answer: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=1))
    # Set once the network answers, even without the file, so neither that nor a slow
    # cut is taken for a network down. Set only through `retries.note_answer`.
    answered: threading.Event = field(default_factory=threading.Event)


def _download_and_cache(file: GoogleFile, fetching: _Fetching) -> None:
    """On its own thread: download `file` and cache it, then hand the copy, or None, on.

    The one waiting may have given up; what's cached is there for the next analysis.
    """
    font_file: bytes | None = None
    try:
        font_file = _download_checked_and_cut(file, fetching)
        # A failure has nothing to cache, and is logged where it happened.
        if font_file is not None:
            _cache_copy(fetching.cached_path, file, font_file)
    finally:  # anything unforeseen still raises, but the one waiting hears now, not later
        fetching.answer.put(font_file)


def _cache_copy(cached_path: Path, file: GoogleFile, font_file: bytes) -> None:
    """Keep `font_file` at `cached_path`, and for a cut, its own hash beside it."""
    _kept(cached_path, font_file)
    # A fixed file is checked by git's hash, which `file` carries.
    if file.weight is None:
        return
    # A cut isn't the file git hashed, so it's checked by its own.
    _kept(_hash_beside(cached_path), blob_hash(font_file).encode())


def _download_checked_and_cut(file: GoogleFile, fetching: _Fetching) -> bytes | None:
    """`file` downloaded, checked against git's hash, and cut if variable; None on a failure."""
    try:
        whole = fetching.download(raw_url(file.path))
    except httpx.HTTPStatusError:  # GitHub answered, without the file: the network works
        _logger.warning(
            "No Google copy of %s: GitHub answered without it", file.path, exc_info=True
        )
        fetching.retries.note_answer(fetching.answered)
        return None
    except Exception:  # no answer: a network fails in many ways; logged, the substitute draws
        _logger.warning("No Google copy of %s: fetch failed", file.path, exc_info=True)
        return None
    # The network works after all: a wait that ran out held back every file for nothing.
    fetching.retries.note_answer(fetching.answered)
    if blob_hash(whole) != file.blob:
        _logger.warning("No Google copy of %s: not the file the pinned commit has", file.path)
        return None
    try:
        return whole if file.weight is None else _cut(whole, file.weight)
    except Exception:  # fontTools raises many kinds on a font it can't cut; logged
        _logger.warning("No Google copy of %s: can't be cut", file.source, exc_info=True)
        return None


def _read_cached_copy(cached_path: Path, file: GoogleFile) -> bytes | None:
    """The cached copy of `file` at `cached_path`; None when it's gone bad.

    Raises FileNotFoundError when it isn't cached, so the caller can count the miss.
    Checked on every read: a copy cut short (a crash before the disk caught up)
    would otherwise be trusted for good. A bad one is deleted where the disk
    allows, and fetched again.
    """
    font_file = cached_path.read_bytes()
    expected_hash = file.blob if file.weight is None else _hash_beside(cached_path).read_text()
    if blob_hash(font_file) == expected_hash:
        return font_file
    _logger.warning("Google's copy of %s in the cache is damaged: fetched again", file.source)
    try:
        cached_path.unlink(missing_ok=True)
    except OSError:  # a read-only disk: the bad copy stays, and is passed over each time
        _logger.warning("Damaged copy of %s can't be deleted", file.source, exc_info=True)
    return None


def _hash_beside(cached_path: Path) -> Path:
    """Where a cut copy's own hash is kept: beside it, under the same name."""
    return cached_path.with_name(f"{cached_path.name}{_HASH_SUFFIX}")


def _cut(variable_font: bytes, weight: int) -> bytes:
    """A variable font fixed at `weight` and the usual width, every other axis at its default.

    MuPDF draws a variable font only at its default weight.
    """
    # No time stamp: two workers cutting one file at once must keep the same bytes.
    font = TTFont(io.BytesIO(variable_font), recalcTimestamp=False)
    axes = {axis.axisTag: axis for axis in font["fvar"].axes}
    wanted = {"wght": weight, "wdth": _WIDTH}
    # Each axis at the value asked for, kept within what the font can do.
    location = {
        tag: min(max(wanted.get(tag, axis.defaultValue), axis.minValue), axis.maxValue)
        for tag, axis in axes.items()
    }
    fixed = instantiateVariableFont(font, location)
    out = io.BytesIO()
    fixed.save(out)
    return out.getvalue()


def _kept(path: Path, font_file: bytes) -> None:
    """Write `font_file` to the cache whole or not at all; a failure is logged, not raised.

    Written beside it, then renamed over it, so a reader never sees half.
    """
    part: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as partial_file:
            part = Path(partial_file.name)
            partial_file.write(font_file)
        os.replace(part, path)
    except OSError:  # a full disk, a read-only one: the copy still lends, uncached
        _logger.warning("Google's copy of %s not cached", path.name, exc_info=True)
        if part is not None:
            part.unlink(missing_ok=True)


def _download(url: str) -> bytes:
    """The bytes at `url`. Raises on a failed request.

    httpx's timeout is per step (connecting, each read), not for the whole
    fetch; `_fetched` keeps the deadline for the whole of it.
    """
    response = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _cache_folder() -> Path:
    """Where the CLI keeps Google's copies: `SQUIDPDF_FONTS`, or the user's cache folder.

    Public fonts only, shared by every document. The server passes its own.
    """
    fonts_folder = os.environ.get("SQUIDPDF_FONTS")  # .get: set only to move the cache
    if fonts_folder:
        return Path(fonts_folder).resolve()
    # .get: set only where the user moved every cache; ~/.cache is the usual place
    user_caches = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(user_caches, "squidpdf", "fonts").resolve()


def google_fonts(*, folder: Path | None = None, cache_only: bool = False) -> Fetch | None:
    """The way to Google's copies: from the cache in `folder`, else fetched and kept there.

    `folder` is `_cache_folder()` unless given, as the server gives its own. With
    `cache_only`, the cache alone, never the network. None when
    SQUIDPDF_NO_FETCH is set: then only the file's own copies lend.
    """
    if os.environ.get(_NO_FETCH):
        return None
    cache = _cache_folder() if folder is None else folder
    github_download = None if cache_only else _download
    return partial(_fetched, folder=cache, download=github_download, retries=_retry_record)
