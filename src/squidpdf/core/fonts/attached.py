from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.embedded import EmbeddedFont, FontUnusable, recalled
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.fonts.pool import FontCopy, Lent, lent_widths, why_unlike_listed
from squidpdf.core.pdf.driver import DriverError, PdfDriver
from squidpdf.core.types import PageFont

_SOURCE = "attached {name}"


class FontFiles(Protocol):
    def __contains__(self, font_name: str, /) -> bool: ...

    def __getitem__(self, font_name: str, /) -> bytes: ...


@dataclass(frozen=True, slots=True, eq=False)
class AttachedFonts:
    driver: PdfDriver
    files: FontFiles = field(repr=False)
    opened: dict[str, EmbeddedFont | FontUnusable] = field(default_factory=dict, repr=False)

    def copy_of(self, own: FontCopy) -> FontCopy | None:
        name = strip_subset(own.font.name)
        if name not in self.files:
            return None
        return self.opened_copy(own.font, name)

    def own_for(self, font: PageFont) -> FontCopy | None:
        name = strip_subset(font.name)
        if name not in self.files:
            return None
        copy = self.opened_copy(font, name)
        why = why_unlike_listed(copy, self.driver.listed_widths(font.font_object))
        if why is not None:
            raise FontUnusable(why)
        return copy

    def opened_copy(self, font: PageFont, name: str) -> FontCopy:
        embedded = recalled(self.opened, name, partial(self._open, name))
        lent = Lent(_SOURCE.format(name=name), name)
        return FontCopy(font, embedded, lent_widths(embedded), lent)

    def _open(self, name: str) -> EmbeddedFont:
        try:
            font_file = self.files[name]
        except KeyError as removed:  # removed since the copies were listed
            raise FontUnusable(Message("attached_removed")) from removed
        try:
            program = self.driver.open_font(font_file)
        except DriverError as problem:
            raise FontUnusable(Message("attached_unreadable")) from problem
        return EmbeddedFont(program, font_file, None)
