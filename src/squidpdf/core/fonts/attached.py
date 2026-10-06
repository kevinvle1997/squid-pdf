"""The user's own copies of a document's fonts: checked like any copy, then lent from.

What neither the file nor Google has, the user often does: Arial, Calibri, a company font.
A copy they attach lends before Google's, and is the own copy of a font the file only names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable, remembered
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.fonts.pool import FontCopy, Lent, previewed_widths, why_unlike_listed
from squidpdf.core.pdf.driver import DriverError, PdfDriver
from squidpdf.core.types import PageFont

_SOURCE = "attached {name}"  # how an attached copy is told from other copies of its font


class FontFiles(Protocol):
    """The user's font files, each asked for by the name of the font it's for; never listed."""

    def __contains__(self, font_name: str, /) -> bool:
        """Whether there's a file for `font_name`."""
        ...

    def __getitem__(self, font_name: str, /) -> bytes:
        """The file for `font_name`. Raises KeyError when there's none."""
        ...


@dataclass(frozen=True, slots=True, eq=False)
class AttachedFonts:
    """The user's copies of a document's fonts, each opened once, when first asked for."""

    driver: PdfDriver  # the document's, whose width lists a named font is checked against
    # Each font file, by the name of the document's font it's for, subset prefix aside.
    files: FontFiles = field(repr=False)
    # Each one opened, or why it can't be, by that name.
    opened: dict[str, EmbeddedFont | FontUnusable] = field(default_factory=dict, repr=False)

    def copy_of(self, own: FontCopy) -> FontCopy | None:
        """The user's copy of the own copy's font, checked like any other; None if none.

        Raises FontUnusable when the file attached can't be opened.
        """
        name = strip_subset(own.font.name)
        # Nothing attached for it: no news, so nothing to say.
        if name not in self.files:
            return None
        return self._copy(own.font, name)

    def own_for(self, font: PageFont) -> FontCopy | None:
        """The user's copy, for a font the page only names; None when they attached none.

        Raises FontUnusable when it isn't the font the page's width list describes.
        """
        name = strip_subset(font.name)
        if name not in self.files:
            return None
        copy = self._copy(font, name)
        why = why_unlike_listed(copy, self.driver.listed_widths(font.xref))
        if why is not None:
            raise FontUnusable(why)
        return copy

    def _copy(self, font: PageFont, name: str) -> FontCopy:
        """The copy attached for `name`, standing for `font`. Raises FontUnusable if no font."""
        embedded = remembered(self.opened, name, partial(self._open, name))
        # Not a font: said as it was the first time it was asked for.
        if isinstance(embedded, FontUnusable):
            raise FontUnusable(embedded.reason)
        lent = Lent(_SOURCE.format(name=name), name)
        return FontCopy(font, embedded, previewed_widths(embedded), lent)

    def _open(self, name: str) -> EmbeddedFont:
        """The file attached for `name`, opened. Raises FontUnusable when it isn't a font."""
        try:
            font_file = self.files[name]
        except KeyError as removed:  # removed since the copies were listed
            raise FontUnusable(Message("attached_removed")) from removed
        try:
            program = self.driver.open_font(font_file)
        except DriverError as problem:  # the library can't read it as a font
            raise FontUnusable(Message("attached_unreadable")) from problem
        return EmbeddedFont(program, font_file, coverage_of(font_file), None)
