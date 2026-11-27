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
    name: str
    weight: int
    italic: bool
    variable: bool
    blob: str


@dataclass(frozen=True, slots=True)
class _Family:
    folder: str
    reserved_name: bool  # its licence reserves its name, which a cut may not carry
    files: tuple[_ListedFile, ...]


@dataclass(frozen=True, slots=True)
class GoogleFile:
    path: str
    blob: str
    weight: int | None

    @property
    def source(self) -> str:
        return self.path if self.weight is None else f"{self.path}@{self.weight}"


@cache
def _family_list() -> dict[str, Any]:
    listed = resources.files("squidpdf").joinpath("fonts", "google-families.json")
    return json.loads(listed.read_text())


def google_file(font: str, descriptor: FontDescriptor | None) -> GoogleFile:
    """Another weight is no copy, though a fixed-width one would pass the width check."""
    family = _family_of(font)
    weight = weight_of(font, descriptor)
    # None of the nine: a file naming a new weight per page would get a cut of each.
    if weight not in WEIGHTS:
        raise FontUnusable(Message("google_no_cut"))
    style, _usual_cut = style_of(font, descriptor)
    return _file_in(family, weight=weight, italic=style in ("italic", "bold-italic"))


def google_family_key(font: str) -> str | None:
    listed = _family_list()
    key = bare_name(font)
    key = listed["aliases"].get(key, key)
    return key if key in listed["families"] else None


def _family_of(font: str) -> _Family:
    key = google_family_key(font)
    if key is None:
        raise FontUnusable(Message("google_not_listed"))
    family = _family_list()["families"][key]
    files = tuple(_ListedFile(*row) for row in family["files"])
    return _Family(family["folder"], family["reserved_name"], files)


def _file_in(family: _Family, *, weight: int, italic: bool) -> GoogleFile:
    same_slant = [file for file in family.files if file.italic == italic]
    fixed = next(
        (file for file in same_slant if not file.variable and file.weight == weight), None
    )
    if fixed is not None:
        return GoogleFile(f"{family.folder}/{fixed.name}", fixed.blob, None)
    variable = next((file for file in same_slant if file.variable), None)
    if variable is None:
        raise FontUnusable(Message("google_no_cut"))
    if family.reserved_name:
        raise FontUnusable(Message("google_name_reserved"))
    return GoogleFile(f"{family.folder}/{variable.name}", variable.blob, weight)
