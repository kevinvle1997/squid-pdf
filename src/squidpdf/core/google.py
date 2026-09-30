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
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, partial
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from squidpdf.core.constants import FETCH_TIMEOUT_S, GOOGLE_FONTS_COMMIT
from squidpdf.core.coverage import Coverage
from squidpdf.core.driver import PdfDriver
from squidpdf.core.embedded import EmbeddedFont
from squidpdf.core.fonts import bare_name, family_and_style, style_of
from squidpdf.core.types import FontDescriptor, PageFont

__all__ = [
    "GoogleFile",
    "Download",
    "Fetch",
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
        if font_file is None:
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


def fetched(file: GoogleFile, *, folder: Path, download: Download) -> bytes | None:
    """Google's copy of `file`, ready to draw with; None when there's none to be had.

    From the cache in `folder` when it's there; else downloaded, checked against
    the blob hash the list records, cut if it's variable, and cached. A failure
    is logged, not raised, and not remembered: the next analysis tries again.
    """
    ready = folder / GOOGLE_FONTS_COMMIT / file.source
    if ready.exists():
        return ready.read_bytes()
    try:
        whole = download(raw_url(file.path))
    except Exception:  # a network fails in many ways; logged, and the stand-in draws
        _logger.warning("No Google copy of %s: fetch failed", file.path, exc_info=True)
        return None
    if blob_hash(whole) != file.blob:
        _logger.warning("No Google copy of %s: not the file the pinned commit has", file.path)
        return None
    font_file = whole if file.weight is None else cut(whole, file.weight)
    kept(ready, font_file)
    return font_file


def cut(variable_font: bytes, weight: int) -> bytes:
    """A variable font fixed at `weight` and the usual width, every other axis at its default.

    MuPDF draws a variable font only at its default weight.
    """
    font = TTFont(io.BytesIO(variable_font))
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
    """Write `font_file` to the cache whole or not at all, so a reader never sees half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as partial_file:
        partial_file.write(font_file)
    os.replace(partial_file.name, path)


def download(url: str) -> bytes:
    """The bytes at `url`. Raises on a failed request, or past FETCH_TIMEOUT_S in all.

    httpx's timeout is per step (connecting, each read), not for the whole
    fetch, and a connection can hang far longer; analysis can't wait on it, so
    the deadline is kept here. A fetch past it is left to finish on its own.
    """
    answer: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)
    # A daemon: one still hanging never keeps the worker from exiting.
    threading.Thread(target=get_into, args=(url, answer), daemon=True).start()
    got = answer.get(timeout=FETCH_TIMEOUT_S)  # raises queue.Empty past the deadline
    if isinstance(got, Exception):
        raise got
    return got


def get_into(url: str, answer: queue.Queue[bytes | Exception]) -> None:
    """Fetch `url` into `answer`: its bytes, or what went wrong."""
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
        answer.put(response.content)
    except Exception as problem:  # noqa: BLE001 (handed to `download`, which raises it)
        answer.put(problem)


def cache_folder() -> Path:
    """Where Google's copies are kept: `SQUIDPDF_FONTS`, or `fonts` in the documents' folder.

    Public fonts only, shared by every document. In the documents' folder by
    default, so it's on the server's kept disk; the sweeper passes over
    anything not named like a document.
    """
    documents = os.environ.get("SQUIDPDF_DATA", "data")  # unset in development
    return Path(os.environ.get("SQUIDPDF_FONTS", f"{documents}/fonts")).resolve()


def google_fonts() -> Fetch | None:
    """The way to Google's copies: from the cache, else fetched and kept there.

    None when SQUIDPDF_NO_FETCH is set: then only the file's own copies lend.
    """
    if os.environ.get(_NO_FETCH):
        return None
    return partial(fetched, folder=cache_folder(), download=download)
