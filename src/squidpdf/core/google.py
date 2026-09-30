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

from squidpdf.core.constants import FETCH_RETRY_S, FETCH_TIMEOUT_S, GOOGLE_FONTS_COMMIT
from squidpdf.core.coverage import Coverage
from squidpdf.core.driver import PdfDriver
from squidpdf.core.embedded import EmbeddedFont
from squidpdf.core.fonts import bare_name, family_and_style, style_of
from squidpdf.core.types import FontDescriptor, PageFont

__all__ = [
    "GoogleFile",
    "Download",
    "Fetch",
    "RetryAt",
    "GoogleFontController",
    "blob_hash",
    "raw_url",
    "fetched",
    "download",
    "google_fonts",
]

_logger = logging.getLogger(__name__)

_RAW = "https://raw.githubusercontent.com/google/fonts"
# Set to anything: never fetch. For tests, and a server with no way out.
_NO_FETCH = "SQUIDPDF_NO_FETCH"
_REGULAR = 400  # the weight a name with no weight word is
_WIDTH = 100  # a variable font's usual width, as `fonts/README.md` cuts ours
_HASH_SUFFIX = ".sha1"  # a cut copy's own hash, in a file beside it
_EVERY_FILE = "*"  # in a RetryAt: a download got no answer, so the network is down
# This process's record of what failed to come: each worker learns on its own.
_retry_at: RetryAt = {}

# A weight word in a font's name, and the weight it means; compound words first,
# so "SemiBold" isn't read as "Bold".
_WEIGHT_WORDS = (
    ("extralight", 200),
    ("ultralight", 200),
    ("semibold", 600),
    ("demibold", 600),
    ("extrabold", 800),
    ("ultrabold", 800),
    ("hairline", 100),
    ("thin", 100),
    ("light", 300),
    ("medium", 500),
    ("bold", 700),
    ("black", 900),
    ("heavy", 900),
)


@dataclass(frozen=True, slots=True)
class ListedFile:
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


# The bytes at a URL; raises when it can't get them. Tests hand in one reading a local file.
type Download = Callable[[str], bytes]
# The engine's way to Google's copy: its bytes, or None when there's none to be had.
type Fetch = Callable[[GoogleFile], bytes | None]
# When each file that failed to come may be tried again, by `GoogleFile.source`, as
# `time.monotonic()` reads; `_EVERY_FILE` holds back all of them.
type RetryAt = dict[str, float]


class GoogleFontController:
    """Google's copy of a document's fonts: matched, fetched and opened, once per file."""

    def __init__(self, driver: PdfDriver, *, fetch: Fetch) -> None:
        """Match fonts in `driver`'s file, and get each file Google has by `fetch`."""
        self._driver = driver
        self._fetch = fetch
        # Each file, opened, or None when there's none to be had, by `GoogleFile.source`.
        self._fonts: dict[str, EmbeddedFont | None] = {}

    def file_for(self, font: PageFont) -> GoogleFile | None:
        """Google's file for one of the document's fonts; None when none fits."""
        return google_file(font.name, self._driver.font_descriptor(font.xref))

    def opened(self, file: GoogleFile) -> EmbeddedFont | None:
        """`file`, fetched and opened once; None when it can't be had or opened."""
        if file.source not in self._fonts:
            self._fonts[file.source] = self._open(file)
        return self._fonts[file.source]

    def _open(self, file: GoogleFile) -> EmbeddedFont | None:
        """`file`, fetched and opened; None when it can't be had or opened."""
        font_file = self._fetch(file)
        # None, or empty: MuPDF would quietly open a font of its own for no bytes.
        if not font_file:
            return None
        try:
            program = self._driver.open_font(font_file)
        except ValueError:  # the library can't read it, though git vouched for the bytes
            return None
        return EmbeddedFont(program, font_file, Coverage(font_file), None)


@cache
def family_list() -> dict[str, Any]:
    """The vendored family list, read once per process."""
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    return json.loads(listed.read_text())


def google_file(font: str, descriptor: FontDescriptor | None) -> GoogleFile | None:
    """The file in Google's collection for a document's font; None when there's none to use.

    Matched by family name, then by weight and style. A family that ships only
    a variable font is cut to the weight, unless its licence reserves its name:
    a cut is a modified version, which may not carry a reserved name.
    """
    listed = family_list()
    key = bare_name(font)
    key = listed["aliases"].get(key, key)  # .get: most families were never renamed
    family = listed["families"].get(key)  # .get: most fonts aren't Google's
    # Not one of Google's families: Arial, Calibri, a TeX font.
    if family is None:
        return None
    weight = weight_of(font, descriptor)
    style, _usual_cut = style_of(font, descriptor)
    italic = style in ("italic", "bold-italic")
    files = [ListedFile(*row) for row in family["files"]]  # rows keep the fields' order
    same_slant = [file for file in files if file.italic == italic]
    folder = family["folder"]
    # A fixed file in this weight and slant.
    fixed = next((f for f in same_slant if not f.variable and f.weight == weight), None)
    if fixed is not None:
        return GoogleFile(f"{folder}/{fixed.name}", fixed.blob, None)
    variable = next((file for file in same_slant if file.variable), None)
    # Neither: no file in this weight and slant.
    if variable is None:
        return None
    # Variable only, but its licence reserves its name, which a cut may not carry.
    if family["reserved_name"]:
        return None
    return GoogleFile(f"{folder}/{variable.name}", variable.blob, weight)


def weight_of(font: str, descriptor: FontDescriptor | None) -> int:
    """A font's weight, 100 to 900: from its name, else its description, else regular."""
    _family, style_words = family_and_style(font)
    words = style_words.replace(" ", "").lower()
    named = next((weight for word, weight in _WEIGHT_WORDS if word in words), None)
    if named is not None:
        return named
    if descriptor is not None and descriptor.weight is not None:
        return int(descriptor.weight)
    return _REGULAR


def blob_hash(data: bytes) -> str:
    """The hash git files `data` under: sha1 of `blob <length>`, a zero byte, then `data`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def raw_url(path: str) -> str:
    """Where GitHub serves `path` in Google's collection, as it was at the pinned commit."""
    return f"{_RAW}/{GOOGLE_FONTS_COMMIT}/{path}"


def fetched(
    file: GoogleFile, *, folder: Path, download: Download | None, retry_at: RetryAt
) -> bytes | None:
    """Google's copy of `file`, ready to draw with; None when there's none to be had.

    From the cache in `folder` when it's there and sound. Else, given `download`,
    downloaded in the background and waited on for FETCH_TIMEOUT_S at most; one
    that finishes later is still checked, cut and cached, for the next analysis.
    A failure is logged, not raised, and noted in `retry_at`, so nothing waits
    on it again for FETCH_RETRY_S. A download that gets no answer holds back
    every file: the network failed, not the file, and each would wait as long.
    """
    ready = folder / GOOGLE_FONTS_COMMIT / file.source
    cached = from_cache(ready, file)
    if cached is not None:
        return cached
    # The cache alone: render and export lend what analysis fetched, and never wait.
    if download is None:
        return None
    now = time.monotonic()
    # .get: most files, and the network, have never failed
    held_until = max(retry_at.get(file.source, 0.0), retry_at.get(_EVERY_FILE, 0.0))
    # Failed a moment ago: not worth another wait yet.
    if now < held_until:
        return None
    fetching = Fetching(ready=ready, download=download, retry_at=retry_at)
    # A daemon: one still hanging never keeps the worker from exiting.
    threading.Thread(target=fetch_into, args=(file, fetching), daemon=True).start()
    try:
        font_file = fetching.answer.get(timeout=FETCH_TIMEOUT_S)
    except queue.Empty:  # not ready by the deadline: it carries on, and caches what it gets
        _logger.warning("No Google copy of %s: not ready in %s s", file.path, FETCH_TIMEOUT_S)
        # No answer from the network holds back every file; a slow cut, only this one.
        held = file.source if fetching.answered.is_set() else _EVERY_FILE
        retry_at[held] = now + FETCH_RETRY_S
        return None
    if font_file is None:
        retry_at[file.source] = now + FETCH_RETRY_S
    return font_file


@dataclass(frozen=True, slots=True)
class Fetching:
    """One download under way in the background, and what it tells the one waiting on it."""

    ready: Path  # where it's cached
    download: Download
    retry_at: RetryAt  # the record of failures, lifted for every file once an answer comes
    # The copy, ready to draw with, or None; put once, whether or not anyone still waits.
    answer: queue.Queue[bytes | None] = field(default_factory=lambda: queue.Queue(maxsize=1))
    # Set once the network has answered, so a slow cut isn't taken for a network down.
    answered: threading.Event = field(default_factory=threading.Event)


def fetch_into(file: GoogleFile, fetching: Fetching) -> None:
    """Download `file`, check it, cut it if variable and cache it; then hand it, or None, on.

    The one waiting may have given up; what's cached is there for the next analysis.
    """
    font_file = checked_and_cut(file, fetching)
    # Nothing to keep: the failure is logged.
    if font_file is None:
        fetching.answer.put(None)
        return
    kept(fetching.ready, font_file)
    # A cut isn't the file git hashed, so its own hash is kept beside it to check it by.
    if file.weight is not None:
        kept(hash_beside(fetching.ready), blob_hash(font_file).encode())
    fetching.answer.put(font_file)


def checked_and_cut(file: GoogleFile, fetching: Fetching) -> bytes | None:
    """`file` downloaded, checked against git's hash, and cut if variable; None on a failure."""
    try:
        whole = fetching.download(raw_url(file.path))
    except Exception:  # a network fails in many ways; logged, and the stand-in draws
        _logger.warning("No Google copy of %s: fetch failed", file.path, exc_info=True)
        return None
    finally:
        fetching.answered.set()
    # The network works after all: a wait that ran out held back every file for nothing.
    fetching.retry_at.pop(_EVERY_FILE, None)
    if blob_hash(whole) != file.blob:
        _logger.warning("No Google copy of %s: not the file the pinned commit has", file.path)
        return None
    try:
        return whole if file.weight is None else cut(whole, file.weight)
    except Exception:  # fontTools raises many kinds on a font it can't cut; logged
        _logger.warning("No Google copy of %s: can't be cut", file.source, exc_info=True)
        return None


def from_cache(ready: Path, file: GoogleFile) -> bytes | None:
    """The cached copy of `file` at `ready`; None when there's none, or it's gone bad.

    Checked on every read: a copy cut short (a crash before the disk caught up)
    would otherwise be trusted for good. A bad one is deleted, to be fetched again.
    """
    try:
        font_file = ready.read_bytes()
        sound = file.blob if file.weight is None else hash_beside(ready).read_text()
    except FileNotFoundError:  # not cached yet, or a cut whose hash was never written
        return None
    if blob_hash(font_file) == sound:
        return font_file
    _logger.warning("Google's copy of %s in the cache is damaged: fetched again", file.source)
    try:
        ready.unlink(missing_ok=True)
    except OSError:  # a read-only disk: the bad copy stays, and is passed over each time
        _logger.warning("Damaged copy of %s can't be deleted", file.source, exc_info=True)
    return None


def hash_beside(ready: Path) -> Path:
    """Where a cut copy's own hash is kept: beside it, under the same name."""
    return ready.with_name(f"{ready.name}{_HASH_SUFFIX}")


def cut(variable_font: bytes, weight: int) -> bytes:
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


def kept(path: Path, font_file: bytes) -> None:
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


def download(url: str) -> bytes:
    """The bytes at `url`. Raises on a failed request.

    httpx's timeout is per step (connecting, each read), not for the whole
    fetch; `fetched` keeps the deadline for the whole of it.
    """
    response = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
    response.raise_for_status()
    return response.content


def cache_folder() -> Path:
    """Where the CLI keeps Google's copies: `SQUIDPDF_FONTS`, or the user's cache folder.

    Public fonts only, shared by every document. The server passes its own.
    """
    moved = os.environ.get("SQUIDPDF_FONTS")  # .get: set only to move the cache
    if moved:
        return Path(moved).resolve()
    # .get: set only where the user moved every cache; ~/.cache is the usual place
    user_caches = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(user_caches, "squidpdf", "fonts").resolve()


def google_fonts(*, folder: Path | None = None, cache_only: bool = False) -> Fetch | None:
    """The way to Google's copies: from the cache in `folder`, else fetched and kept there.

    `folder` is `cache_folder()` unless given, as the server gives its own. With
    `cache_only`, the cache alone, never the network. None when
    SQUIDPDF_NO_FETCH is set: then only the file's own copies lend.
    """
    if os.environ.get(_NO_FETCH):
        return None
    cache = cache_folder() if folder is None else folder
    way_out = None if cache_only else download
    return partial(fetched, folder=cache, download=way_out, retry_at=_retry_at)
