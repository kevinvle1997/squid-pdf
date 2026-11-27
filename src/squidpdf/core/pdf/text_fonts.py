"""MuPDF's text names a font only by name: its listing cut short, or the name in its file."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from squidpdf.core.types import PageFont

_NAME_CUT_AT = 31  # bytes of a font's name MuPDF keeps for the text it reads
_PREFIX_PLUS_AT = 6


@dataclass(slots=True)
class PageTextFonts:
    listed: dict[str, PageFont]
    embedded: dict[str, PageFont] | None = None

    def font_of(
        self, read_as: str, by_embedded_name: Callable[[], dict[str, PageFont]]
    ) -> PageFont | None:
        listed = self.listed.get(read_as)
        if listed is not None:
            return listed
        if self.embedded is None:
            self.embedded = by_embedded_name()
        return self.embedded.get(read_as)

    def name_of(self, read_as: str) -> str:
        listed = self.listed.get(read_as)
        return read_as if listed is None else as_pymupdf_names(listed.name)


def page_text_fonts(fonts: list[PageFont]) -> PageTextFonts:
    listed: dict[str, PageFont] = {}
    by_cut: dict[str, dict[str, PageFont]] = {}
    for font in fonts:
        whole = as_pymupdf_names(font.name)
        listed.setdefault(whole, font)
        by_cut.setdefault(as_pymupdf_names(_cut_of(font.name)), {}).setdefault(whole, font)
    # A cut two names share is no listing's: nothing tells them apart.
    unique_cuts = {
        cut: font
        for cut, wholes in by_cut.items()
        if len(wholes) == 1
        for font in wholes.values()
    }
    return PageTextFonts(unique_cuts | listed)


def as_pymupdf_names(name: str) -> str:
    """Its own rule, not `strip_subset`'s: a "+" anywhere else stays."""
    has_prefix = name[_PREFIX_PLUS_AT : _PREFIX_PLUS_AT + 1] == "+"
    return name[_PREFIX_PLUS_AT + 1 :] if has_prefix else name


def _cut_of(name: str) -> str:
    return name[:_NAME_CUT_AT]
