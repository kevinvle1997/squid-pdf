"""The document's fonts: each span's own, pooled, its look-alike, and its gaps.

Read through the driver once and kept, each copy once per listing. A span's font is its
page's copy pooled with every other copy in the file (`core.fonts.pool`), then the user's
copy, then Google's, each lending what the copies before it lack.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import pairwise
from statistics import median

from squidpdf.core.app.message import Message
from squidpdf.core.constants import BASELINE_EPS, LETTER_SPACING_MIN_EM
from squidpdf.core.fonts.attached import AttachedFonts, FontFiles
from squidpdf.core.fonts.embedded import FontUnusable, made_once, open_embedded, remembered
from squidpdf.core.fonts.google import Fetch, GoogleFontController
from squidpdf.core.fonts.look_alike import kind_in_file, look_alike
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
from squidpdf.core.text.spacing import lacks_space, usual_gap
from squidpdf.core.types import (
    EM,
    DrawnGlyph,
    Face,
    FontCode,
    LookAlike,
    PageFont,
    Rect,
    Span,
    TextPiece,
    same_font,
)

# The glyph a ligature's later letters are read with: the one before drew them.
_LIGATURE_TAIL = -1


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
    """What one page says about its fonts, and what's worked out from them and its text.

    Each read or worked out when the page is first asked for it.
    """

    # The page's fonts, in the library's order, read before an edit can drop one.
    fonts: list[PageFont]
    # The same fonts by their PDF object, which is how text names the font that draws it.
    by_object: dict[int, PageFont]
    # The page's usual gap for a space, by the font's object and name, and the font measured in.
    usual_gaps: dict[tuple[int | None, str, FontProgram], float] = field(default_factory=dict)
    # A span's font with its other copies, by the page's copy of it, or why it can't be.
    pools: dict[PageFont, PooledFont | FontUnusable] = field(default_factory=dict, repr=False)

    def usual_gap(
        self, key: tuple[int | None, str, FontProgram], make: Callable[[], float]
    ) -> float:
        """The usual gap for a space, by its font and the font measured in, on first use."""
        return made_once(self.usual_gaps, key, make)

    def pool(self, font: PageFont, make: Callable[[], PooledFont]) -> PooledFont | FontUnusable:
        """A span's font pooled with its other copies, made on first use, or why it can't be."""
        return remembered(self.pools, font, make)


@dataclass(frozen=True, slots=True, eq=False)
class _GlyphsByHeight:
    """Letters a page draws, top first, to find those whose baseline starts in a box."""

    tops: list[float]  # each one's baseline, in order
    glyphs: list[DrawnGlyph]

    def inside(self, box: Rect) -> Iterator[DrawnGlyph]:
        """Each letter whose baseline starts inside `box`, top first, each line as drawn."""
        first, past = bisect_left(self.tops, box.y0), bisect_right(self.tops, box.y1)
        for glyph in self.glyphs[first:past]:
            x, _y = glyph.origin
            if box.x0 <= x <= box.x1:
                yield glyph


@dataclass(frozen=True, slots=True, eq=False)
class _PageGlyphs:
    """The letters one page draws, and which page."""

    page: int
    letters: _GlyphsByHeight = field(repr=False)


def _by_height(glyphs: list[DrawnGlyph]) -> _GlyphsByHeight:
    """`glyphs`, sorted by their baselines; on one, in the order drawn (a stable sort)."""
    ordered = sorted(glyphs, key=lambda glyph: glyph.origin[1])
    return _GlyphsByHeight([glyph.origin[1] for glyph in ordered], ordered)


@dataclass(slots=True)
class _FontCache:
    """What has been looked up about the document's fonts, each filled in on first use.

    A copy of a font, and the name its copies share, by how its page lists it
    (its object, its name there, whether a form uses it), so pages that list it
    alike share one. A page's facts and text by page number; every letter, by that name.
    """

    # What each page says, by page number, read when the page is first asked about.
    pages: dict[int, _PageFacts] = field(default_factory=dict)
    # Each page's text as it was, by page number: kept from the index, else read when needed.
    # Apart from `pages`, so the index keeps a page's text without reading its fonts.
    lines: dict[int, list[list[TextPiece]]] = field(default_factory=dict, repr=False)
    # Each copy of a font in the file, opened, or why it can't be used.
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict)
    # The name each font's copies share, read on first use: every walk asks it of every font.
    copies_names: dict[PageFont, str] = field(default_factory=dict)
    # Each font's look-alike, by its name and object (None: not on the page).
    look_alikes: dict[tuple[str, int | None], LookAlike] = field(default_factory=dict)
    # Every letter a font's copies draw, by the name they share, once a walk opened them all.
    every_letter: dict[str, frozenset[str]] = field(default_factory=dict)
    # The letters the page last asked about draws: one page's, since a page's are many,
    # and spans are judged page by page.
    glyphs: _PageGlyphs | None = field(default=None, repr=False)

    def page(self, number: int, make: Callable[[], _PageFacts]) -> _PageFacts:
        """What page `number` says, read on first use."""
        return made_once(self.pages, number, make)

    def text_lines(
        self, page: int, make: Callable[[], list[list[TextPiece]]]
    ) -> list[list[TextPiece]]:
        """Page `page`'s text as it was, read on first use unless it was kept."""
        return made_once(self.lines, page, make)

    def keep_text_lines(self, page: int, lines: list[list[TextPiece]]) -> None:
        """Keep page `page`'s text as it was, read by someone else first."""
        self.lines[page] = lines

    def copy(self, font: PageFont, make: Callable[[], FontCopy]) -> FontCopy | FontUnusable:
        """One copy of a font in the file, opened on first use, or why it can't be used."""
        return remembered(self.copies, font, make)

    def copies_name(self, font: PageFont, make: Callable[[], str]) -> str:
        """The name a font's copies share, read on first use."""
        return made_once(self.copies_names, font, make)

    def look_alike(
        self, key: tuple[str, int | None], make: Callable[[], LookAlike]
    ) -> LookAlike:
        """A font's look-alike, by its name and object, chosen on first use."""
        return made_once(self.look_alikes, key, make)

    def every_letter_of(self, name: str) -> frozenset[str] | None:
        """Every letter of the font `name`; None until known."""
        # .get: no pool may have walked every copy yet.
        return self.every_letter.get(name)

    def keep_every_letter(self, name: str, letters: frozenset[str]) -> None:
        """Keep every letter of the font `name`."""
        self.every_letter[name] = letters

    def page_glyphs(self, page: int, make: Callable[[], list[DrawnGlyph]]) -> _GlyphsByHeight:
        """The letters `page` draws, with their shapes, read unless it's the page last asked."""
        if self.glyphs is None or self.glyphs.page != page:
            self.glyphs = _PageGlyphs(page, _by_height(make()))
        return self.glyphs.letters

    def forget_pages(self) -> None:
        """Forget everything, as a fresh cache: it was all looked up through page numbers.

        For after the pages are renumbered: a page's facts, and the copies,
        their letters and look-alikes found through them, are read again from the pages.
        """
        self.pages.clear()
        self.lines.clear()
        self.copies.clear()
        self.copies_names.clear()
        self.look_alikes.clear()
        self.every_letter.clear()
        self.glyphs = None


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
        page_font = self._page_font(span)
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

    def keep_text_lines(self, page: int, lines: list[list[TextPiece]]) -> None:
        """Keep the page's text as the index read it."""
        self.cache.keep_text_lines(page, lines)

    def same_widths(self, span: Span, face: Face) -> bool:
        """Whether `face` sets the span's font's letters as wide as it does: a look-alike so."""
        match = self.look_alike(span)
        return match.same_widths and face == match.face

    def listed_widths(self, span: Span) -> dict[str, float] | None:
        """Each letter the page's width list gives the span's font, per 1000 em; None without.

        Raises DriverError when the library can't load the font.
        """
        page_font = self._page_font(span)
        # New text, or a font the page doesn't list: no list to read.
        if page_font is None:
            return None
        return self.driver.listed_widths(page_font.font_object)

    def read_before_erasing(self, span: Span) -> None:
        """Read what a draw over `span` asks of its font, while its page still has the font."""
        own = self.own(span)
        self.look_alike(span)
        # Measured in the page's own gaps: none of its own to measure.
        if own is not None and lacks_space(own):
            self.usual_gap(span, own)

    def _page_font(self, span: Span) -> PageFont | None:
        """The page's font that draws the span, by its object; None when the page has none."""
        # A font the page doesn't list, or new text in a face we ship.
        if span.font_object is None:
            return None
        return self._facts(span.page).by_object[span.font_object]

    def _facts(self, page: int) -> _PageFacts:
        """What the page says, its fonts read the first time it's asked about."""
        return self.cache.page(page, partial(self._read_facts, page))

    def _read_facts(self, page: int) -> _PageFacts:
        """Read the page's fonts, and list each by its object."""
        fonts = self.driver.fonts(page)
        by_object: dict[int, PageFont] = {}
        # The library's order: a font listed twice, under two resource names, is its first.
        for font in fonts:
            by_object.setdefault(font.font_object, font)
        return _PageFacts(fonts, by_object)

    def font_object_named(
        self, page: int, name: str, *, font_object: int | None = None
    ) -> int | None:
        """The object of the page's font that goes by `name`, for new text in it; None if none.

        `font_object` first, when the page has it: two fonts can read by one name. Else as
        the page's text names it, which the browser is told; else as the page lists it.
        """
        if font_object in self._facts(page).by_object:
            return font_object
        pieces = _each_piece(self._page_lines(page))
        named = (piece.font_object for piece in pieces if piece.font == name)
        in_text = next((font_object for font_object in named if font_object is not None), None)
        if in_text is not None:
            return in_text
        wanted = strip_subset(name)
        listed = (font for font in self._facts(page).fonts if strip_subset(font.name) == wanted)
        return next((font.font_object for font in listed), None)

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

        The file's other copies, nearest page first; then copies from outside the file, only
        while `letters` (the pool's so far) lacks one someone could type, each as the copy or
        why it can't be had. A face we ship is the last resort, not a lender.
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
        embedded, coverage = open_embedded(self.driver, font)
        return font_copy(font, embedded, coverage)

    def _other_copies(self, page: int, own: PageFont) -> Iterator[PageFont]:
        """Every other copy of the font in the file: each font that goes by its copies' name.

        This page's first, then the nearest page's: the order a letter is
        borrowed in. A page's fonts are read only when the walk reaches it.
        """
        font_name = self._copies_name(own)
        # The own copy may be listed under another name, found by the one its text reads.
        seen = {own.font_object}
        for font in self._fonts_nearest_first(page):
            is_other_copy = (
                font.font_object not in seen and self._copies_name(font) == font_name
            )
            if is_other_copy:
                seen.add(font.font_object)  # one font object on several pages is one copy
                yield font

    def _copies_name(self, font: PageFont) -> str:
        """The name every copy of the font goes by, subset prefix aside, read once."""
        return self.cache.copies_name(font, partial(self._read_copies_name, font))

    def _read_copies_name(self, font: PageFont) -> str:
        """The name every copy of the font goes by, subset prefix aside.

        A Type3 font's own name is often its object's, so no two copies share it:
        the font its description names stands in, when it names one.
        """
        descriptor = (
            self.driver.font_descriptor(font.font_object) if font.kind == "type3" else None
        )
        # Its own name: not a Type3, or a Type3 whose description names no font.
        if descriptor is None or descriptor.name is None:
            return strip_subset(font.name)
        return strip_subset(descriptor.name)

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
        page_font = self._page_font(span)
        # Not on the page: new text, in no font of the document's.
        if page_font is None:
            return Message("font_not_in_file")
        name = strip_subset(page_font.name)
        candidate = AttachedFonts(self.driver, {name: font_file})
        own = self._opened(page_font)
        try:
            # Only named: checked against the page's width list. Type3 isn't stored, yet draws.
            only_named = isinstance(own, FontUnusable) and not page_font.is_embedded
            if only_named:
                candidate.own_for(page_font)
                return None
            # Stored, but no copy we can use: nothing to check it against.
            if isinstance(own, FontUnusable):
                return own.reason
            in_file = pooled_font(own, partial(self._file_copies, span.page, own))
            copy = candidate.opened_copy(own.font, name)
        except FontUnusable as refused:  # it isn't the font, or isn't a font at all
            return refused.reason
        return why_turned_away(copy, own=own, letters=in_file.every_letter())

    def _file_copies(
        self, page: int, own: FontCopy, letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy]:
        """The file's other copies of the own copy's font, nearest page first.

        Once a walk has seen every copy, a later one stops when the pool's `letters`
        hold all theirs: a copy left could lend only a letter it has.
        """
        name = self._copies_name(own.font)
        every_letter = self.cache.every_letter_of(name)
        drawn = set(own.widths)
        for font in self._other_copies(page, own.font):
            pool_has_them_all = every_letter is not None and letters.keys() >= every_letter
            if pool_has_them_all:
                return
            copy = self._opened(font)
            # A copy we can't open lends no letters; the span's own still draws what it can.
            if isinstance(copy, FontCopy):
                drawn.update(copy.widths)
                yield copy
        self.cache.keep_every_letter(name, frozenset(drawn))

    def shown_name(self, span: Span) -> str:
        """The name the reader knows the span's font by, subset prefix aside.

        A Type3 font's own name is often its object's, so it goes by its copies' name.
        """
        page_font = self._page_font(span)
        if page_font is not None and page_font.kind == "type3":
            return self._copies_name(page_font)
        return strip_subset(span.font)

    def look_alike(self, span: Span) -> LookAlike:
        """The look-alike for the span's font, in its style: the face we ship we'd use."""
        page_font = self._page_font(span)
        font_object = None if page_font is None else page_font.font_object
        key = (span.font, font_object)
        return self.cache.look_alike(key, partial(self._look_alike_of, span, font_object))

    def _look_alike_of(self, span: Span, font_object: int | None) -> LookAlike:
        """The look-alike for the span's font, stored as object `font_object`, if it is."""
        # New text, or a font the page doesn't list: go by the name alone.
        descriptor = None if font_object is None else self.driver.font_descriptor(font_object)
        own = self.own(span)
        # The file's own copy says what kind of font it is where the description doesn't.
        file_kind = None if own is None else kind_in_file(own.own.embedded.file)
        return look_alike(span.font, descriptor, file_kind=file_kind)

    def substitute(self, span: Span, text: str) -> Substitute:
        """The face that draws `text` when the span's own font can't, and what it leaves out."""
        return substitute_for(self.look_alike(span).face, text)

    def drawn_codes(self, span: Span, own: PooledFont) -> dict[str, FontCode]:
        """The code each letter of the span is drawn with, where its own copy has several.

        Read from the page, so a redraw picks the shape the line shows: a list's tabular "1."
        """
        coded = own.own.embedded.coded
        # Written by letter, or one code a letter: nothing to choose, and nothing to read.
        if coded is None or not coded.has_alternates(span.text):
            return {}
        by_shape = {(code.letter, code.glyph): code for code in coded.codes}
        glyphs = self.cache.page_glyphs(span.page, partial(self.driver.drawn_glyphs, span.page))
        codes: dict[str, FontCode] = {}
        for glyph in glyphs.inside(span.bbox):
            # .get: a ligature's later letters, or a letter another font draws in the box.
            code = by_shape.get((glyph.letter, glyph.glyph))
            # Drawn first wins: a line draws a letter one way.
            if code is not None:
                codes.setdefault(glyph.letter, code)
        return codes

    def letter_spacing(self, span: Span, own: PooledFont | None) -> float:
        """How far past its width the page sets each letter of the span apart, per em.

        Each letter measured as a redraw measures it, in `own`, and by the page's own box for
        it, which a narrowed line narrows: kept only where both agree, and past kerning.
        """
        # New text, or a turned line: no letters of its own, or none set apart along the page.
        if not span.fragments or span.turned:
            return 0.0
        glyphs = self.cache.page_glyphs(span.page, partial(self.driver.drawn_glyphs, span.page))
        # In the order drawn: a line drawn twice over, as for a shadow, starts again.
        pairs = [(a, b) for a, b in pairwise(glyphs.inside(span.bbox)) if _within_a_word(a, b)]
        # A letter alone, or only spaces between: nothing to read.
        if not pairs:
            return 0.0
        width_of = partial(_DrawnWidths.of(own).width, size=span.size)
        by_widths = median(
            (b.origin[0] - a.origin[0] - width_of(a)) / span.size for a, b in pairs
        )
        by_boxes = median((b.origin[0] - a.origin[0] - a.width) / span.size for a, b in pairs)
        spaced = abs(by_widths) >= LETTER_SPACING_MIN_EM
        # Narrowed, not spaced: each letter's box is as narrow as it's set apart.
        agreed = abs(by_widths - by_boxes) < LETTER_SPACING_MIN_EM
        return by_widths if spaced and agreed else 0.0

    def usual_gap(self, span: Span, font: FontProgram) -> float:
        """The page's usual gap for a space in the span's font, measured in `font`.

        Kept by the span's font and the font measured in, what it's worked out
        from; the page's text is read once, before any edit.
        """
        measure = partial(self._usual_gap_on, span, font=font)
        key = (span.font_object, span.font, font)
        return self._facts(span.page).usual_gap(key, measure)

    def _usual_gap_on(self, span: Span, *, font: FontProgram) -> float:
        """The usual gap for a space in the span's font, on its page, measured in `font`."""
        lines = self._page_lines(span.page)
        in_font = [piece for piece in _each_piece(lines) if same_font(piece, span)]
        return usual_gap(in_font, font=font)

    def _page_lines(self, page: int) -> list[list[TextPiece]]:
        """The page's text as the index read it, else when first asked for, line by line."""
        return self.cache.text_lines(page, partial(self.driver.text_lines, page))


@dataclass(frozen=True, slots=True)
class _DrawnWidths:
    """The widths a redraw measures the span's own copy in, per 1000 em."""

    by_glyph: dict[int, float]  # written by code: each code's, by the shape it picks
    by_letter: dict[str, float]  # written by letter: each letter's

    @classmethod
    def of(cls, own: PooledFont | None) -> _DrawnWidths:
        """`own`'s own copy's widths; none for a font the file only names."""
        # Not in the file: the page's own measure is all there is.
        if own is None:
            return cls({}, {})
        coded = own.own.embedded.coded
        codes = () if coded is None else coded.codes
        # Lowest code first: a shape two codes pick has the first one's width.
        by_glyph: dict[int, float] = {}
        for code in codes:
            by_glyph.setdefault(code.glyph, code.width)
        return cls(by_glyph, own.own.widths)

    def width(self, glyph: DrawnGlyph, *, size: float) -> float:
        """How far `glyph` moves the pen at `size`; the page's own measure if none is listed."""
        listed = self.by_glyph.get(glyph.glyph, self.by_letter.get(glyph.letter))
        return glyph.width if listed is None else listed * size / EM


def _within_a_word(letter: DrawnGlyph, following: DrawnGlyph) -> bool:
    """Whether two letters drawn in a row are set apart only by letter spacing.

    Not across a space, whose gap is a word's, nor a ligature, whose later letters draw
    nothing, nor back to a line's start, where it's drawn again.
    """
    drawn = letter.glyph != _LIGATURE_TAIL and following.glyph != _LIGATURE_TAIL
    letters = not letter.letter.isspace() and not following.letter.isspace()
    level = abs(letter.origin[1] - following.origin[1]) <= BASELINE_EPS
    onward = following.origin[0] > letter.origin[0]
    return drawn and letters and level and onward


def _each_piece(lines: list[list[TextPiece]]) -> Iterator[TextPiece]:
    """Every piece of a page's text, line by line."""
    for line in lines:
        yield from line
