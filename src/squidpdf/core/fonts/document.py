"""The document's fonts: each span's own, pooled, its look-alike, and its gaps.

What the engine knows about fonts, read through the driver once and kept. A
copy of a font is opened once per listing, not once per page, so a font every
page shares is read and parsed once. A span's font is its page's copy pooled
with every other copy of it in the file (`core.fonts.pool`), so a letter one copy
lacks can come from another, and last with Google's copy, if it has one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.embedded import FontUnusable, open_embedded, remembered
from squidpdf.core.fonts.google import Fetch, GoogleFontController
from squidpdf.core.fonts.look_alike import look_alike, strip_subset
from squidpdf.core.fonts.pool import (
    FontCopy,
    PooledFont,
    font_copy,
    google_copy,
    lacks_a_keyboard_letter,
)
from squidpdf.core.fonts.substitute import Substitute, substitute_for
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from squidpdf.core.text.spacing import usual_gap
from squidpdf.core.types import LookAlike, PageFont, Span, TextPiece

__all__ = [
    "FontSources",
    "NO_SOURCES",
    "FontCache",
    "DocumentFonts",
]


@dataclass(frozen=True, slots=True)
class FontSources:
    """Where a document's fonts may borrow letters from outside the file, chosen at the edge.

    One record from `open_pdf` down, so a new source is one more field here, not
    another keyword through every signature on the way.
    """

    google: Fetch | None = None  # Google's copies, fetched or read from the cache


# No sources: only the file's own copies lend.
NO_SOURCES = FontSources()


@dataclass(slots=True)
class PageFacts:
    """What one page says about its fonts and text, read when the page is first asked about."""

    # The page's fonts, in the library's order, read before an edit can drop one.
    fonts: list[PageFont]
    # The same fonts by the name the page lists them under, subset prefix aside.
    listed: dict[str, PageFont]
    # Its stored fonts by the name their text reads, where that's another: None until needed.
    read_as: dict[str, PageFont] | None = None
    # The page's text as it was: None until needed.
    lines: list[list[TextPiece]] | None = None
    # The page's usual gap for a space, by the font's name and the font it's measured in.
    usual_gaps: dict[tuple[str, FontProgram], float] = field(default_factory=dict)


@dataclass(slots=True)
class FontCache:
    """What has been looked up about the document's fonts, each filled in on first use.

    A copy of a font by how its page lists it (its object, its name there,
    whether a form uses it), so pages that list it alike share one. A page's
    own facts by page number.
    """

    # What each page says, by page number, read when the page is first asked about.
    pages: dict[int, PageFacts] = field(default_factory=dict)
    # Each copy of a font in the file, opened, or why it can't be used.
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict)
    # A span's font with its other copies, by its page and its page's copy.
    pools: dict[tuple[int, PageFont], PooledFont | FontUnusable] = field(default_factory=dict)
    # Each font's look-alike, by its name and object (None: not on the page).
    look_alikes: dict[tuple[str, int | None], LookAlike] = field(default_factory=dict)

    def pool(
        self, key: tuple[int, PageFont], make: Callable[[], PooledFont]
    ) -> PooledFont | FontUnusable:
        """A span's font pooled with its other copies, made on first use, or why it can't be."""
        return remembered(self.pools, key, make)

    def copy(self, font: PageFont, make: Callable[[], FontCopy]) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened on first use, or why it can't be used."""
        return remembered(self.copies, font, make)


class DocumentFonts:
    """The fonts a document's spans are written in, and the look-alike we ship for each."""

    def __init__(self, driver: PdfDriver, *, google: GoogleFontController | None) -> None:
        """Read through `driver`, with nothing looked up yet.

        Without `google`, only the file's own copies of a font lend it letters.
        """
        self._driver = driver
        self._google = google
        self._cache = FontCache()

    def lookup(self, span: Span) -> PooledFont | FontUnusable:
        """The span's font, pooled with its other copies in the file, or why we can't use it."""
        page_font = self.page_font(span)
        # Not on the page: new text in a font it doesn't have.
        if page_font is None:
            return FontUnusable(Message("font_not_in_file"))
        make_pool = partial(self._pool, span.page, page_font)
        return self._cache.pool((span.page, page_font), make_pool)

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

    def _facts(self, page: int) -> PageFacts:
        """What the page says, its fonts read the first time it's asked about."""
        if page not in self._cache.pages:
            fonts = self._driver.fonts(page)
            listed: dict[str, PageFont] = {}
            # The library's order: the first by a name wins.
            for font in fonts:
                listed.setdefault(strip_subset(font.name), font)
            self._cache.pages[page] = PageFacts(fonts, listed)
        return self._cache.pages[page]

    def _listed(self, page: int) -> dict[str, PageFont]:
        """The page's fonts by the name it lists each under."""
        return self._facts(page).listed

    def _read_as(self, page: int) -> dict[str, PageFont]:
        """The page's stored fonts by the name their text reads, found when first needed."""
        facts = self._facts(page)
        if facts.read_as is None:
            by_name: dict[str, PageFont] = {}
            # The library's order again: the first by a name wins.
            for font in facts.fonts:
                read_as = self._driver.text_font_name(font.xref) if font.is_embedded else None
                if read_as is not None:
                    by_name.setdefault(strip_subset(read_as), font)
            facts.read_as = by_name
        return facts.read_as

    def _pool(self, page: int, page_font: PageFont) -> PooledFont:
        """Open the page's copy of the font, then pool the copies that lend it letters with it.

        Raises FontUnusable, saying why, when the page's copy can't be used.
        """
        own = self._opened(page_font)
        if isinstance(own, FontUnusable):
            raise FontUnusable(own.reason)
        return PooledFont(own, partial(self._lenders, page, own))

    def _lenders(
        self, page: int, own: FontCopy, letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy]:
        """Every copy that may lend the own copy letters, in the order they lend: the chain.

        The file's other copies, nearest page first; then copies from outside
        the file, only while `letters`, those the pool has so far, lacks one
        someone could type. A face we ship is the engine's last resort, not a lender.
        """
        opened = (self._opened(font) for font in self._other_copies(page, own.font))
        # A copy we can't open lends no letters; the span's own still draws what it can.
        yield from (copy for copy in opened if isinstance(copy, FontCopy))
        # From outside the file, in order. The user's own copy of a font, once
        # they can attach one, goes before Google's. Without Google, there are none.
        from_outside = (
            () if self._google is None else (partial(self._google_copy, self._google),)
        )
        for copy_from in from_outside:
            # Only a letter someone could type is worth fetching a copy for.
            if not lacks_a_keyboard_letter(letters):
                return
            copy = copy_from(own)
            # No copy of this font to be had there, and why: it lends nothing, as above.
            if isinstance(copy, FontCopy):
                yield copy

    def _google_copy(
        self, google: GoogleFontController, own: FontCopy
    ) -> FontCopy | FontUnusable:
        """Google's copy of the own copy's font, or why it has none, or it can't be had."""
        file = google.file_for(own.font)
        # Not one of Google's families, or a cut it may not make.
        if isinstance(file, FontUnusable):
            return file
        embedded = google.opened(file)
        # Not fetched, or not readable.
        if isinstance(embedded, FontUnusable):
            return embedded
        return google_copy(own, embedded, file)

    def _opened(self, font: PageFont) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened once, or why we can't use it."""
        return self._cache.copy(font, partial(self._open_copy, font))

    def _open_copy(self, font: PageFont) -> FontCopy:
        """Open one copy of a font in the file. Raises FontUnusable, saying why, if we can't.

        It may not be stored, be unreadable, or have no way to be written to.
        """
        return font_copy(font, open_embedded(self._driver, font))

    def _other_copies(self, page: int, own: PageFont) -> Iterator[PageFont]:
        """Every other font in the file by the same name, subset prefix aside.

        This page's first, then the nearest page's: the order a letter is
        borrowed in. A page's fonts are read only when the walk reaches it.
        """
        font_name = strip_subset(own.name)
        # The own copy may be listed under another name, found by the one its text reads.
        seen = {own.xref}
        for font in self._fonts_nearest_first(page):
            is_other_copy = font.xref not in seen and strip_subset(font.name) == font_name
            if is_other_copy:
                seen.add(font.xref)  # one font object on several pages is one copy
                yield font

    def _fonts_nearest_first(self, page: int) -> Iterator[PageFont]:
        """Every page's fonts, this page's, then the nearest page's, each read when reached."""
        page_count = self._driver.page_count()
        nearest_first = sorted(
            range(page_count), key=lambda other_page: (abs(other_page - page), other_page)
        )
        for other_page in nearest_first:
            yield from self._facts(other_page).fonts

    def look_alike(self, span: Span) -> LookAlike:
        """The look-alike for the span's font, in its style: the face we ship we'd use."""
        page_font = self.page_font(span)
        xref = None if page_font is None else page_font.xref
        key = (strip_subset(span.font), xref)
        if key not in self._cache.look_alikes:
            # New text, or a font the page doesn't list: go by the name alone.
            descriptor = None if xref is None else self._driver.font_descriptor(xref)
            self._cache.look_alikes[key] = look_alike(span.font, descriptor)
        return self._cache.look_alikes[key]

    def substitute(self, span: Span, text: str) -> Substitute:
        """The face that draws `text` when the span's own font can't, and what it leaves out."""
        return substitute_for(self.look_alike(span).face, text)

    def usual_gap(self, span: Span, font: FontProgram) -> float:
        """The page's usual gap for a space in the span's font, measured in `font`.

        Kept by the font's name and the font measured in, what it's worked out
        from; the page's text is read once, before any edit.
        """
        font_name = strip_subset(span.font)
        gaps = self._facts(span.page).usual_gaps
        key = (font_name, font)
        if key not in gaps:
            lines = self.page_lines(span.page)
            gaps[key] = usual_gap(lines, font_name=font_name, font=font)
        return gaps[key]

    def page_lines(self, page: int) -> list[list[TextPiece]]:
        """The page's text as it was when first asked for, line by line."""
        facts = self._facts(page)
        if facts.lines is None:
            facts.lines = self._driver.text_lines(page)
        return facts.lines
