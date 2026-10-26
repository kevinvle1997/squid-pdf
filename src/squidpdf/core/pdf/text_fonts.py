"""How MuPDF's text names the font of each piece it reads, and a page's fonts by those names.

Its text names a piece's font only by name: the page's listing of it, cut short and without a
subset prefix, or the name inside the font's own file. Names in, fonts out: no MuPDF call here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from squidpdf.core.types import PageFont

_NAME_CUT_AT = 31  # bytes of a font's name MuPDF keeps for the text it reads
_PREFIX_PLUS_AT = 6  # where PyMuPDF looks for the "+" that ends a subset prefix


@dataclass(slots=True)
class PageTextFonts:
    """A page's fonts by the name its text reads for each, to find the one a piece is in.

    Made by `page_text_fonts`. Two fonts whose text reads one name both give the first.
    """

    listed: dict[str, PageFont]  # each by the name it's listed under, as its text reads that
    # Each stored one by the name inside its file: None until a piece reads a name not listed.
    embedded: dict[str, PageFont] | None = None

    def font_of(
        self, read_as: str, by_embedded_name: Callable[[], dict[str, PageFont]]
    ) -> PageFont | None:
        """The font whose text reads `read_as`, as listed or as named inside; None if none."""
        # .get: a name the page doesn't list, as the one inside a font's own file.
        listed = self.listed.get(read_as)
        if listed is not None:
            return listed
        if self.embedded is None:
            self.embedded = by_embedded_name()
        # .get: a font the page doesn't list, such as one only a form field's own drawing uses.
        return self.embedded.get(read_as)

    def name_of(self, read_as: str) -> str:
        """The piece's font's name: as the page lists it, where its text reads that cut short.

        Else as its text reads it: the name inside the font's file, or a cut two listings share.
        """
        # .get: a name the page doesn't list as it reads, as above.
        listed = self.listed.get(read_as)
        return read_as if listed is None else as_pymupdf_names(listed.name)


def page_text_fonts(fonts: list[PageFont]) -> PageTextFonts:
    """`fonts` by the name text reads each listing as: whole, or a cut only one has."""
    listed: dict[str, PageFont] = {}
    by_cut: dict[str, dict[str, PageFont]] = {}
    # The library's order: of two listed under one name, as subsets of one face, the first wins.
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
    """`name` as PyMuPDF names a piece's font: without a subset prefix it finds.

    Its own rule, not `strip_subset`'s: a "+" anywhere else stays.
    """
    has_prefix = name[_PREFIX_PLUS_AT : _PREFIX_PLUS_AT + 1] == "+"
    return name[_PREFIX_PLUS_AT + 1 :] if has_prefix else name


def _cut_of(name: str) -> str:
    """`name` as MuPDF keeps it for the text it reads; PyMuPDF reads each byte as a letter."""
    return name[:_NAME_CUT_AT]
