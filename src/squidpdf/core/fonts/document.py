"""The document's fonts: each span's own, pooled, its look-alike, and its gaps.

What the engine knows about fonts, read through the driver once and kept, each copy
once per listing. A span's font is its page's copy pooled with every other copy of it
in the file (`core.fonts.pool`), then the user's copy, then Google's, each lending what
the copies before it lack.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.attached import AttachedFonts, FontFiles
from squidpdf.core.fonts.embedded import FontUnusable, made_once, open_embedded, remembered
from squidpdf.core.fonts.google import Fetch, GoogleFontController
from squidpdf.core.fonts.look_alike import look_alike
from squidpdf.core.fonts.names import strip_subset
from squidpdf.core.fonts.pool import (
    FontCopy,
    Lender,
    PooledFont,
    font_copy,
    lacks_a_keyboard_letter,
    pooled_font,
    why_turned_away,
)
from squidpdf.core.fonts.substitute import Substitute, substitute_for
from squidpdf.core.pdf.driver import FontProgram, PdfDriver
from squidpdf.core.text.spacing import usual_gap
from squidpdf.core.types import LookAlike, PageFont, Span, TextPiece


@dataclass(frozen=True, slots=True, eq=False)
class FontSources:
    """Where a document's fonts may borrow letters from outside the file, chosen at the edge.

    One record from `open_pdf` down, so a new source is one more field here, not
    another keyword through every signature on the way. `eq=False`: it holds font files.
    """

    google: Fetch | None = None  # Google's copies, fetched or read from the cache
    # The user's own copies, by the name of the document's font each is for, subset aside.
    attached: FontFiles = field(default_factory=dict[str, bytes], repr=False)


# No sources: only the file's own copies lend.
NO_SOURCES = FontSources()


@dataclass(slots=True)
class _PageFacts:
    """What one page says about its fonts and text, and what's worked out from them.

    Each read or worked out when the page is first asked for it.
    """

    # The page's fonts, in the library's order, read before an edit can drop one.
    fonts: list[PageFont]
    # The same fonts by the name the page lists them under, subset prefix aside.
    listed: dict[str, PageFont]
    # Its stored fonts by the name their text reads, where that's another: None until needed.
    read_as: dict[str, PageFont] | None = None
    # The page's text as it was: None until needed.
    lines: list[list[TextPiece]] | None = field(default=None, repr=False)
    # The page's usual gap for a space, by the font's name and the font it's measured in.
    usual_gaps: dict[tuple[str, FontProgram], float] = field(default_factory=dict)
    # A span's font with its other copies, by the page's copy of it, or why it can't be.
    pools: dict[PageFont, PooledFont | FontUnusable] = field(default_factory=dict, repr=False)

    def fonts_read_as(self, make: Callable[[], dict[str, PageFont]]) -> dict[str, PageFont]:
        """Its stored fonts by the name their text reads, found on first use."""
        if self.read_as is None:
            self.read_as = make()
        return self.read_as

    def text_lines(self, make: Callable[[], list[list[TextPiece]]]) -> list[list[TextPiece]]:
        """The page's text as it was, read on first use."""
        if self.lines is None:
            self.lines = make()
        return self.lines

    def usual_gap(self, key: tuple[str, FontProgram], make: Callable[[], float]) -> float:
        """The usual gap for a space, by font name and the font measured in, on first use."""
        return made_once(self.usual_gaps, key, make)

    def pool(self, font: PageFont, make: Callable[[], PooledFont]) -> PooledFont | FontUnusable:
        """A span's font pooled with its other copies, made on first use, or why it can't be."""
        return remembered(self.pools, font, make)


@dataclass(slots=True)
class _FontCache:
    """What has been looked up about the document's fonts, each filled in on first use.

    A copy of a font by how its page lists it (its object, its name there,
    whether a form uses it), so pages that list it alike share one. A page's
    own facts by page number.
    """

    # What each page says, by page number, read when the page is first asked about.
    pages: dict[int, _PageFacts] = field(default_factory=dict)
    # Each copy of a font in the file, opened, or why it can't be used.
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict)
    # Each font's look-alike, by its name and object (None: not on the page).
    look_alikes: dict[tuple[str, int | None], LookAlike] = field(default_factory=dict)

    def page(self, number: int, make: Callable[[], _PageFacts]) -> _PageFacts:
        """What page `number` says, read on first use."""
        return made_once(self.pages, number, make)

    def copy(self, font: PageFont, make: Callable[[], FontCopy]) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened on first use, or why it can't be used."""
        return remembered(self.copies, font, make)

    def look_alike(
        self, key: tuple[str, int | None], make: Callable[[], LookAlike]
    ) -> LookAlike:
        """A font's look-alike, by its name and object, chosen on first use."""
        return made_once(self.look_alikes, key, make)

    def forget_pages(self) -> None:
        """Forget everything, as a fresh cache: it was all looked up through page numbers.

        For after the pages are renumbered: a page's facts, and the copies and
        look-alikes found through them, are read again from the pages as they are.
        """
        self.pages.clear()
        self.copies.clear()
        self.look_alikes.clear()


@dataclass(frozen=True, slots=True, eq=False)
class DocumentFonts:
    """The fonts a document's spans are written in, and the look-alike we ship for each."""

    driver: PdfDriver
    # The user's own copies of the document's fonts, if they attached any.
    attached: AttachedFonts
    # Google's copies of the document's fonts. None: only the file's and the user's lend.
    google: GoogleFontController | None
    cache: _FontCache = field(default_factory=_FontCache, repr=False)

    def forget_pages(self) -> None:
        """Forget what was looked up: the pages were just renumbered."""
        self.cache.forget_pages()

    def lookup(self, span: Span) -> PooledFont | FontUnusable:
        """The span's font, pooled with its other copies in the file, or why we can't use it."""
        page_font = self.page_font(span)
        # Not on the page: new text in a font it doesn't have.
        if page_font is None:
            return FontUnusable(Message("font_not_in_file"))
        make_pool = partial(self._pool, span.page, page_font)
        return self._facts(span.page).pool(page_font, make_pool)

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

    def _facts(self, page: int) -> _PageFacts:
        """What the page says, its fonts read the first time it's asked about."""
        return self.cache.page(page, partial(self._read_facts, page))

    def _read_facts(self, page: int) -> _PageFacts:
        """Read the page's fonts, and list each under its name."""
        fonts = self.driver.fonts(page)
        listed: dict[str, PageFont] = {}
        # The library's order: the first by a name wins.
        for font in fonts:
            listed.setdefault(strip_subset(font.name), font)
        return _PageFacts(fonts, listed)

    def _listed(self, page: int) -> dict[str, PageFont]:
        """The page's fonts by the name it lists each under."""
        return self._facts(page).listed

    def _read_as(self, page: int) -> dict[str, PageFont]:
        """The page's stored fonts by the name their text reads, found when first needed."""
        facts = self._facts(page)
        return facts.fonts_read_as(partial(self._by_text_name, facts.fonts))

    def _by_text_name(self, fonts: list[PageFont]) -> dict[str, PageFont]:
        """The stored ones of `fonts` by the name their text reads."""
        by_name: dict[str, PageFont] = {}
        # The library's order again: the first by a name wins.
        for font in fonts:
            read_as = self.driver.text_font_name(font.xref) if font.is_embedded else None
            if read_as is not None:
                by_name.setdefault(strip_subset(read_as), font)
        return by_name

    def _pool(self, page: int, page_font: PageFont) -> PooledFont:
        """Open the page's copy of the font, then pool the copies that lend it letters with it.

        A font the page only names has the user's copy as its own, if they attached
        one. Raises FontUnusable, saying why, when there's no copy to use.
        """
        own = self._opened(page_font)
        only_named = isinstance(own, FontUnusable) and not page_font.is_embedded
        # Only named: the user's copy is its own, checked against the page's width list.
        if only_named:
            attached = self.attached.own_for(page_font)
            own = own if attached is None else attached
        if isinstance(own, FontUnusable):
            raise FontUnusable(own.reason)
        return pooled_font(own, partial(self._lenders, page, own))

    def _lenders(
        self, page: int, own: FontCopy, letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy | FontUnusable]:
        """Every copy that may lend the own copy letters, in the order they lend: the chain.

        The file's other copies, nearest page first; then copies from outside
        the file, only while `letters`, those the pool has so far, lacks one
        someone could type, each as the copy or why there's none to be had. A
        face we ship is the engine's last resort, not a lender.
        """
        yield from self._file_copies(page, own, letters)
        # From outside the file, in order: the user's copy goes before Google's, saving a fetch.
        from_outside: list[Lender] = [self.attached]
        if self.google is not None:
            from_outside.append(self.google)
        for lender in from_outside:
            # Only a letter someone could type is worth fetching a copy for.
            if not lacks_a_keyboard_letter(letters):
                return
            try:
                lent: FontCopy | FontUnusable | None = lender.copy_of(own)
            except FontUnusable as no_copy:  # raised when there's no copy to be had there
                lent = no_copy
            # Without a copy there, nothing is lent, but why goes to the pool, to be said.
            if lent is not None:
                yield lent

    def _opened(self, font: PageFont) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened once, or why we can't use it."""
        return self.cache.copy(font, partial(self._open_copy, font))

    def _open_copy(self, font: PageFont) -> FontCopy:
        """Open one copy of a font in the file. Raises FontUnusable, saying why, if we can't."""
        return font_copy(font, open_embedded(self.driver, font))

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
        page_count = self.driver.page_count()
        nearest_first = sorted(
            range(page_count), key=lambda other_page: (abs(other_page - page), other_page)
        )
        for other_page in nearest_first:
            yield from self._facts(other_page).fonts

    def why_not_its_font(self, span: Span, font_file: bytes) -> Message | None:
        """Why `font_file` isn't the span's font, as the user's copy of it; None when it is.

        Checked against the file's copies of it, every one, or the page's width list
        when it's only named: the rule every copy that lends is held to.
        """
        page_font = self.page_font(span)
        # Not on the page: new text, in no font of the document's.
        if page_font is None:
            return Message("font_not_in_file")
        candidate = AttachedFonts(self.driver, {strip_subset(page_font.name): font_file})
        own = self._opened(page_font)
        try:
            # Only named: checked against the page's width list.
            only_named = isinstance(own, FontUnusable) and not page_font.is_embedded
            if only_named:
                candidate.own_for(page_font)
                return None
            # Stored, but no copy we can use: nothing to check it against.
            if isinstance(own, FontUnusable):
                return own.reason
            in_file = pooled_font(own, partial(self._file_copies, span.page, own))
            copy = candidate.copy_of(own)
        except FontUnusable as refused:  # it isn't the font, or isn't a font at all
            return refused.reason
        # The span's font has no name `copy_of` keys by: not the user's to attach.
        if copy is None:
            return Message("font_not_in_file")
        return why_turned_away(copy, own=own, letters=in_file.letters)

    def _file_copies(
        self, page: int, own: FontCopy, _letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy]:
        """The file's other copies of the own copy's font, nearest page first.

        Takes the pool's letters so far, as `pooled_font` hands a lender: not needed here.
        """
        opened = (self._opened(font) for font in self._other_copies(page, own.font))
        # A copy we can't open lends no letters; the span's own still draws what it can.
        return (copy for copy in opened if isinstance(copy, FontCopy))

    def look_alike(self, span: Span) -> LookAlike:
        """The look-alike for the span's font, in its style: the face we ship we'd use."""
        page_font = self.page_font(span)
        xref = None if page_font is None else page_font.xref
        key = (strip_subset(span.font), xref)
        return self.cache.look_alike(key, partial(self._look_alike_of, span.font, xref))

    def _look_alike_of(self, font_name: str, xref: int | None) -> LookAlike:
        """The look-alike for the font named `font_name`, stored as object `xref`, if it is."""
        # New text, or a font the page doesn't list: go by the name alone.
        descriptor = None if xref is None else self.driver.font_descriptor(xref)
        return look_alike(font_name, descriptor)

    def substitute(self, span: Span, text: str) -> Substitute:
        """The face that draws `text` when the span's own font can't, and what it leaves out."""
        return substitute_for(self.look_alike(span).face, text)

    def usual_gap(self, span: Span, font: FontProgram) -> float:
        """The page's usual gap for a space in the span's font, measured in `font`.

        Kept by the font's name and the font measured in, what it's worked out
        from; the page's text is read once, before any edit.
        """
        font_name = strip_subset(span.font)
        measure = partial(self._usual_gap_on, span.page, font_name=font_name, font=font)
        return self._facts(span.page).usual_gap((font_name, font), measure)

    def _usual_gap_on(self, page: int, *, font_name: str, font: FontProgram) -> float:
        """The page's usual gap for a space in `font_name`, subset prefix aside, in `font`."""
        lines = self.page_lines(page)
        in_font = [
            piece for piece in _each_piece(lines) if strip_subset(piece.font) == font_name
        ]
        return usual_gap(in_font, font=font)

    def page_lines(self, page: int) -> list[list[TextPiece]]:
        """The page's text as it was when first asked for, line by line."""
        return self._facts(page).text_lines(partial(self.driver.text_lines, page))


def _each_piece(lines: list[list[TextPiece]]) -> Iterator[TextPiece]:
    """Every piece of a page's text, line by line."""
    for line in lines:
        yield from line
