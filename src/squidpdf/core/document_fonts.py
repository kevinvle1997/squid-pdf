"""The document's fonts: each span's own, pooled, the face that stands in for it, and its gaps.

What the engine knows about fonts, read through the driver once and kept. A
copy of a font is opened once per listing, not once per page, so a font every
page shares is read and parsed once. A span's font is its page's copy pooled
with every other copy of it in the file (`core.pooled`), so a letter one copy
lacks can come from another.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from squidpdf.core import faces
from squidpdf.core.driver import FontProgram, PdfDriver
from squidpdf.core.embedded import FontUnusable, open_embedded
from squidpdf.core.fonts import look_alike, strip_subset
from squidpdf.core.message import Message
from squidpdf.core.pooled import FontCopy, PooledFont, font_copy, pooled
from squidpdf.core.spacing import usual_gap
from squidpdf.core.types import LookAlike, PageFont, Span, TextPiece

__all__ = [
    "FontCache",
    "DocumentFonts",
]


@dataclass(slots=True)
class FontCache:
    """What has been looked up about the document's fonts, each filled in on first use.

    A copy of a font by how its page lists it (its object, its name there,
    whether a form uses it), so pages that list it alike share one. A page's
    own facts by page number.
    """

    # Every page's fonts, read once, before an edit can drop one: None until asked for.
    page_fonts: list[list[PageFont]] | None = None
    # Each page's fonts by the name the page lists them under, subset prefix aside.
    listed: dict[int, dict[str, PageFont]] = field(default_factory=dict)
    # Each page's stored fonts by the name their text reads, where that's another.
    read_as: dict[int, dict[str, PageFont]] = field(default_factory=dict)
    # Each copy of a font in the file, opened, or why it can't be used.
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict)
    # A span's font with its other copies, by its page and its page's copy.
    pools: dict[tuple[int, PageFont], PooledFont | FontUnusable] = field(default_factory=dict)
    # The face that stands in for each font, by its name and object (None: not on the page).
    look_alikes: dict[tuple[str, int | None], LookAlike] = field(default_factory=dict)
    # The page's usual gap for a space, in a font with none of its own.
    usual_gaps: dict[tuple[int, str], float] = field(default_factory=dict)
    # Each page's text as it was, read once.
    lines: dict[int, list[list[TextPiece]]] = field(default_factory=dict)


class DocumentFonts:
    """The fonts a document's spans are written in, and the faces we ship that stand in."""

    def __init__(self, driver: PdfDriver) -> None:
        """Read through `driver`, with nothing looked up yet."""
        self._driver = driver
        self._cache = FontCache()

    def lookup(self, span: Span) -> PooledFont | FontUnusable:
        """The span's font, pooled with its other copies in the file, or why we can't use it."""
        page_font = self.page_font(span)
        # Not on the page: new text in a font it doesn't have.
        if page_font is None:
            return FontUnusable(Message("font_not_in_file"))
        key = (span.page, page_font)
        if key not in self._cache.pools:
            try:
                self._cache.pools[key] = self._pool(span.page, page_font)
            except FontUnusable as problem:  # no copy of the font we can use, and why
                self._cache.pools[key] = problem
        return self._cache.pools[key]

    def own(self, span: Span) -> PooledFont | None:
        """The span's font, pooled with its other copies in the file; None when we can't use it.

        Only named, not stored, is the common case for anything exported from
        Word, and it is exactly what the substitute state warns about.
        """
        found = self.lookup(span)
        return found if isinstance(found, PooledFont) else None

    def why_not(self, span: Span) -> Message | None:
        """Why the span's own font can't be used; None when it can."""
        found = self.lookup(span)
        return found.reason if isinstance(found, FontUnusable) else None

    def page_font(self, span: Span) -> PageFont | None:
        """The page's font by the span's font name, subset prefix aside; None when it has none.

        By the name the page lists it under, else by the one its text reads: a
        font stored in the file can be read under the name inside it, as ours
        are once an export redraws with them. The first by a name, in the
        library's order; any other is pooled with it.
        """
        name = strip_subset(span.font)
        # .get: new text can name a font the page doesn't have.
        listed = self._listed(span.page).get(name)
        return listed if listed is not None else self._read_as(span.page).get(name)

    def _listed(self, page: int) -> dict[str, PageFont]:
        """The page's fonts by the name it lists each under."""
        if page not in self._cache.listed:
            by_name: dict[str, PageFont] = {}
            # The library's order: the first by a name wins.
            for font in self._page_fonts()[page]:
                by_name.setdefault(strip_subset(font.name), font)
            self._cache.listed[page] = by_name
        return self._cache.listed[page]

    def _read_as(self, page: int) -> dict[str, PageFont]:
        """The page's stored fonts by the name their text reads, found when first needed."""
        if page not in self._cache.read_as:
            by_name: dict[str, PageFont] = {}
            # The library's order again: the first by a name wins.
            for font in self._page_fonts()[page]:
                read_as = self._driver.text_font_name(font.xref) if font.is_embedded else None
                if read_as is not None:
                    by_name.setdefault(strip_subset(read_as), font)
            self._cache.read_as[page] = by_name
        return self._cache.read_as[page]

    def _pool(self, page: int, page_font: PageFont) -> PooledFont:
        """Open the page's copy of the font, then pool the file's other copies with it.

        Raises FontUnusable, saying why, when the page's copy can't be used.
        """
        own = self._opened(page_font)
        if isinstance(own, FontUnusable):
            raise FontUnusable(own.reason)
        others = (self._opened(font) for font in self._other_copies(page, own.font))
        # A copy we can't open lends no letters; the span's own still draws what it can.
        return pooled(own, (copy for copy in others if isinstance(copy, FontCopy)))

    def _opened(self, font: PageFont) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened once, or why we can't use it."""
        if font not in self._cache.copies:
            try:
                self._cache.copies[font] = font_copy(font, open_embedded(self._driver, font))
            except FontUnusable as problem:  # not stored, unreadable, or no way to write it
                self._cache.copies[font] = problem
        return self._cache.copies[font]

    def _other_copies(self, page: int, own: PageFont) -> list[PageFont]:
        """Every other font in the file by the same name, subset prefix aside.

        This page's first, then the nearest page's: the order a letter is borrowed in.
        """
        font_name = strip_subset(own.name)
        page_fonts = self._page_fonts()
        nearest_first = sorted(
            range(len(page_fonts)), key=lambda other: (abs(other - page), other)
        )
        same_name = (
            font
            for other in nearest_first
            for font in page_fonts[other]
            if strip_subset(font.name) == font_name
        )
        copies: dict[int, PageFont] = {}
        for font in same_name:
            copies.setdefault(font.xref, font)  # one font object on several pages is one copy
        # .pop: the own copy may be listed under another name, found by the one its text reads.
        copies.pop(own.xref, None)
        return list(copies.values())

    def _page_fonts(self) -> list[list[PageFont]]:
        """Every page's fonts, read once, before an edit can drop one."""
        if self._cache.page_fonts is None:
            page_count = self._driver.page_count()
            self._cache.page_fonts = [self._driver.fonts(page) for page in range(page_count)]
        return self._cache.page_fonts

    def look_alike(self, span: Span) -> LookAlike:
        """The face we ship that stands in for the span's font, in its style."""
        page_font = self.page_font(span)
        xref = None if page_font is None else page_font.xref
        key = (strip_subset(span.font), xref)
        if key not in self._cache.look_alikes:
            # New text, or a font the page doesn't list: go by the name alone.
            descriptor = None if xref is None else self._driver.font_descriptor(xref)
            self._cache.look_alikes[key] = look_alike(span.font, descriptor)
        return self._cache.look_alikes[key]

    def stand_in(self, span: Span, text: str) -> faces.StandIn:
        """The face that draws `text` when the span's own font can't, and what it leaves out."""
        return faces.stand_in(self.look_alike(span).face, text)

    def usual_gap(self, span: Span, font: FontProgram) -> float:
        """The page's usual gap for a space in the span's font, read once, before any edit."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.usual_gaps:
            lines = self.page_lines(span.page)
            self._cache.usual_gaps[key] = usual_gap(lines, font_name=font_name, font=font)
        return self._cache.usual_gaps[key]

    def page_lines(self, page: int) -> list[list[TextPiece]]:
        """The page's text as it was when first asked for, line by line."""
        if page not in self._cache.lines:
            self._cache.lines[page] = self._driver.text_lines(page)
        return self._cache.lines[page]
