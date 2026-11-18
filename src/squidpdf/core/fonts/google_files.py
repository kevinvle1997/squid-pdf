"""Which file in Google's collection is a document's font, matched offline by the vendored list.

The family list, from one pinned commit, ships as `fonts/google-families.json`
(`scripts/google_families.py` rebuilds it), so matching needs no network.
Fetching, caching and opening the file are `core.fonts.google`'s.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.embedded import FontUnusable
from squidpdf.core.fonts.names import WEIGHTS, bare_name, style_of, weight_of
from squidpdf.core.types import FontDescriptor


@dataclass(frozen=True, slots=True)
class _ListedFile:
    """One file of a family, as the vendored list has it."""

    name: str  # e.g. "Poppins-Bold.ttf", or "OpenSans[wdth,wght].ttf"
    weight: int  # 100 to 900; a variable file's default
    italic: bool
    variable: bool  # one file for every weight, to be cut to one
    blob: str  # git's hash of it at the pinned commit


@dataclass(frozen=True, slots=True)
class _Family:
    """One of Google's families, as the vendored list has it."""

    folder: str  # under the commit, e.g. "ofl/poppins"
    reserved_name: bool  # its licence reserves its name, which a cut may not carry
    files: tuple[_ListedFile, ...]


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


@cache
def _family_list() -> dict[str, Any]:
    """The vendored family list, read once per process."""
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    return json.loads(listed.read_text())


def google_file(font: str, descriptor: FontDescriptor | None) -> GoogleFile:
    """The file in Google's collection for a document's font, in its own weight and style.

    A file of another weight is no copy, though a fixed-width one would pass the
    width check. Raises FontUnusable, saying why, when there's none to use.
    """
    family = _family_of(font)
    weight = weight_of(font, descriptor)
    # None of the nine: a file naming a new weight per page would get a cut of each.
    if weight not in WEIGHTS:
        raise FontUnusable(Message("google_no_cut"))
    style, _usual_cut = style_of(font, descriptor)
    return _file_in(family, weight=weight, italic=style in ("italic", "bold-italic"))


def google_family_key(font: str) -> str | None:
    """The key Google's pinned list files a document font's family under; None if not there."""
    listed = _family_list()
    key = bare_name(font)
    key = listed["aliases"].get(key, key)  # .get: most families were never renamed
    return key if key in listed["families"] else None


def _family_of(font: str) -> _Family:
    """Google's family of a document's font. Raises FontUnusable when it has none."""
    key = google_family_key(font)
    # Not one of Google's families: Arial, Calibri, a TeX font.
    if key is None:
        raise FontUnusable(Message("google_not_listed"))
    family = _family_list()["families"][key]
    files = tuple(_ListedFile(*row) for row in family["files"])  # rows keep the fields' order
    return _Family(family["folder"], family["reserved_name"], files)


def _file_in(family: _Family, *, weight: int, italic: bool) -> GoogleFile:
    """The family's file in this weight and slant: a fixed one, else its variable one, cut.

    Raises FontUnusable when it has neither, or may not be cut.
    """
    same_slant = [file for file in family.files if file.italic == italic]
    # A fixed file in this weight and slant.
    fixed = next(
        (file for file in same_slant if not file.variable and file.weight == weight), None
    )
    if fixed is not None:
        return GoogleFile(f"{family.folder}/{fixed.name}", fixed.blob, None)
    variable = next((file for file in same_slant if file.variable), None)
    # Neither: no file in this weight and slant.
    if variable is None:
        raise FontUnusable(Message("google_no_cut"))
    # Variable only, but its licence reserves its name, which a cut may not carry.
    if family.reserved_name:
        raise FontUnusable(Message("google_name_reserved"))
    return GoogleFile(f"{family.folder}/{variable.name}", variable.blob, weight)
