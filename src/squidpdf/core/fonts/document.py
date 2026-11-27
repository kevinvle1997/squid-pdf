from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import pairwise
from statistics import median

import squidpdf.core.constants as constants
from squidpdf.core.app.message import Message
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

# A ligature's later letters are read with this glyph: the one before drew them.
_LIGATURE_TAIL = -1


@dataclass(frozen=True, slots=True, eq=False)
class FontSources:
    google: Fetch | None = None
    attached: FontFiles = field(default_factory=dict[str, bytes], repr=False)


NO_SOURCES = FontSources()


@dataclass(slots=True)
class _PageFacts:
    fonts: list[PageFont]
    by_object: dict[int, PageFont]
    usual_gaps: dict[tuple[int | None, str, FontProgram], float] = field(
        default_factory=dict, repr=False
    )
    pools: dict[PageFont, PooledFont | FontUnusable] = field(default_factory=dict, repr=False)

    def usual_gap(
        self, key: tuple[int | None, str, FontProgram], make: Callable[[], float]
    ) -> float:
        return made_once(self.usual_gaps, key, make)

    def pool(self, font: PageFont, make: Callable[[], PooledFont]) -> PooledFont | FontUnusable:
        return remembered(self.pools, font, make)


@dataclass(frozen=True, slots=True, eq=False)
class _GlyphsByHeight:
    tops: list[float]
    glyphs: list[DrawnGlyph]

    def inside(self, box: Rect) -> Iterator[DrawnGlyph]:
        first, past = bisect_left(self.tops, box.y0), bisect_right(self.tops, box.y1)
        for glyph in self.glyphs[first:past]:
            x, _y = glyph.origin
            if box.x0 <= x <= box.x1:
                yield glyph


@dataclass(frozen=True, slots=True, eq=False)
class _PageGlyphs:
    page: int
    letters: _GlyphsByHeight = field(repr=False)


def _by_height(glyphs: list[DrawnGlyph]) -> _GlyphsByHeight:
    ordered = sorted(glyphs, key=lambda glyph: glyph.origin[1])
    return _GlyphsByHeight([glyph.origin[1] for glyph in ordered], ordered)


@dataclass(slots=True)
class _FontCache:
    pages: dict[int, _PageFacts] = field(default_factory=dict, repr=False)
    # Apart from `pages`, so the index keeps a page's text without reading its fonts.
    lines: dict[int, list[list[TextPiece]]] = field(default_factory=dict, repr=False)
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict, repr=False)
    copies_names: dict[PageFont, str] = field(default_factory=dict, repr=False)
    look_alikes: dict[tuple[int | None, str], LookAlike] = field(
        default_factory=dict, repr=False
    )
    every_letter: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    # One page's, since a page's are many, and spans are judged page by page.
    glyphs: _PageGlyphs | None = field(default=None, repr=False)

    def page(self, number: int, make: Callable[[], _PageFacts]) -> _PageFacts:
        return made_once(self.pages, number, make)

    def text_lines(
        self, page: int, make: Callable[[], list[list[TextPiece]]]
    ) -> list[list[TextPiece]]:
        return made_once(self.lines, page, make)

    def keep_text_lines(self, page: int, lines: list[list[TextPiece]]) -> None:
        self.lines[page] = lines

    def copy(self, font: PageFont, make: Callable[[], FontCopy]) -> FontCopy | FontUnusable:
        return remembered(self.copies, font, make)

    def copies_name(self, font: PageFont, make: Callable[[], str]) -> str:
        return made_once(self.copies_names, font, make)

    def look_alike(
        self, key: tuple[int | None, str], make: Callable[[], LookAlike]
    ) -> LookAlike:
        return made_once(self.look_alikes, key, make)

    def every_letter_of(self, copies_name: str) -> frozenset[str] | None:
        return self.every_letter.get(copies_name)

    def keep_every_letter(self, copies_name: str, letters: frozenset[str]) -> None:
        self.every_letter[copies_name] = letters

    def page_glyphs(self, page: int, make: Callable[[], list[DrawnGlyph]]) -> _GlyphsByHeight:
        if self.glyphs is None or self.glyphs.page != page:
            self.glyphs = _PageGlyphs(page, _by_height(make()))
        return self.glyphs.letters

    def forget_pages(self) -> None:
        """For after the pages are renumbered: all of it was looked up through page numbers."""
        self.pages.clear()
        self.lines.clear()
        self.copies.clear()
        self.copies_names.clear()
        self.look_alikes.clear()
        self.every_letter.clear()
        self.glyphs = None


@dataclass(frozen=True, slots=True, eq=False)
class DocumentFonts:
    driver: PdfDriver
    attached: AttachedFonts
    google: GoogleFontController | None
    cache: _FontCache = field(default_factory=_FontCache, repr=False)

    def forget_pages(self) -> None:
        self.cache.forget_pages()

    def lookup(self, span: Span) -> PooledFont | FontUnusable:
        page_font = self._page_font(span)
        if page_font is None:
            return FontUnusable(Message("font_not_in_file"))
        make_pool = partial(self._pool, span.page, page_font)
        return self._facts(span.page).pool(page_font, make_pool)

    def own(self, span: Span) -> PooledFont | None:
        """Only named, not stored, is the common case for anything exported from Word."""
        found = self.lookup(span)
        return found if isinstance(found, PooledFont) else None

    def keep_text_lines(self, page: int, lines: list[list[TextPiece]]) -> None:
        self.cache.keep_text_lines(page, lines)

    def same_widths(self, span: Span, face: Face) -> bool:
        match = self.look_alike(span)
        return match.same_widths and face == match.face

    def listed_widths(self, span: Span) -> dict[str, float] | None:
        page_font = self._page_font(span)
        if page_font is None:
            return None
        return self.driver.listed_widths(page_font.font_object)

    def read_before_erasing(self, span: Span) -> None:
        own = self.own(span)
        self.look_alike(span)
        if own is not None and lacks_space(own):
            self.usual_gap(span, own)

    def _page_font(self, span: Span) -> PageFont | None:
        if span.font_object is None:
            return None
        return self._facts(span.page).by_object[span.font_object]

    def _facts(self, page: int) -> _PageFacts:
        return self.cache.page(page, partial(self._read_facts, page))

    def _read_facts(self, page: int) -> _PageFacts:
        fonts = self.driver.fonts(page)
        by_object: dict[int, PageFont] = {}
        # The library's order: a font listed twice, under two resource names, is its first.
        for font in fonts:
            by_object.setdefault(font.font_object, font)
        return _PageFacts(fonts, by_object)

    def font_object_named(
        self, page: int, name: str, *, font_object: int | None = None
    ) -> int | None:
        """`font_object` first, when the page has it: two fonts can read by one name."""
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
        own = self._opened(page_font)
        only_named = isinstance(own, FontUnusable) and not page_font.is_embedded
        if only_named:
            attached = self.attached.own_for(page_font)
            own = own if attached is None else attached
        if isinstance(own, FontUnusable):
            raise FontUnusable(own.reason)
        return pooled_font(own, partial(self._lenders, page, own))

    def _lenders(
        self, page: int, own: FontCopy, letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy | FontUnusable]:
        """A face we ship is the last resort, not a lender."""
        yield from self._file_copies(page, own, letters)
        from_outside: list[Lender] = [self.attached]
        if self.google is not None:
            from_outside.append(self.google)
        for lender in from_outside:
            # Only a letter someone could type is worth fetching a copy for.
            if not lacks_a_keyboard_letter(letters):
                return
            try:
                lent: FontCopy | FontUnusable | None = lender.copy_of(own)
            except FontUnusable as no_copy:
                lent = no_copy
            if lent is not None:
                yield lent

    def _opened(self, font: PageFont) -> FontCopy | FontUnusable:
        return self.cache.copy(font, partial(self._open_copy, font))

    def _open_copy(self, font: PageFont) -> FontCopy:
        embedded, coverage = open_embedded(self.driver, font)
        return font_copy(font, embedded, coverage)

    def _other_copies(self, page: int, own: PageFont) -> Iterator[PageFont]:
        copies_name = self._copies_name(own)
        # The own copy may be listed under another name, found by the one its text reads.
        seen = {own.font_object}
        for font in self._fonts_nearest_first(page):
            is_other_copy = (
                font.font_object not in seen and self._copies_name(font) == copies_name
            )
            if is_other_copy:
                seen.add(font.font_object)
                yield font

    def _copies_name(self, font: PageFont) -> str:
        return self.cache.copies_name(font, partial(self._read_copies_name, font))

    def _read_copies_name(self, font: PageFont) -> str:
        """A Type3's own name is its object's: the font its description names stands in."""
        descriptor = (
            self.driver.font_descriptor(font.font_object) if font.kind == "type3" else None
        )
        if descriptor is None or descriptor.name is None:
            return strip_subset(font.name)
        return strip_subset(descriptor.name)

    def _fonts_nearest_first(self, page: int) -> Iterator[PageFont]:
        page_count = self.driver.page_count()
        nearest_first = sorted(
            range(page_count), key=lambda other_page: (abs(other_page - page), other_page)
        )
        for other_page in nearest_first:
            yield from self._facts(other_page).fonts

    def why_not_its_font(self, span: Span, font_file: bytes) -> Message | None:
        page_font = self._page_font(span)
        if page_font is None:
            return Message("font_not_in_file")
        name = strip_subset(page_font.name)
        candidate = AttachedFonts(self.driver, {name: font_file})
        own = self._opened(page_font)
        try:
            only_named = isinstance(own, FontUnusable) and not page_font.is_embedded
            if only_named:
                candidate.own_for(page_font)
                return None
            if isinstance(own, FontUnusable):
                return own.reason
            in_file = pooled_font(own, partial(self._file_copies, span.page, own))
            copy = candidate.opened_copy(own.font, name)
        except FontUnusable as refused:
            return refused.reason
        return why_turned_away(copy, own=own, letters=in_file.every_letter())

    def _file_copies(
        self, page: int, own: FontCopy, letters: Mapping[str, FontCopy]
    ) -> Iterator[FontCopy]:
        """A copy left could lend only a letter it has, so a walk that saw all stops early."""
        copies_name = self._copies_name(own.font)
        every_letter = self.cache.every_letter_of(copies_name)
        drawn = set(own.widths)
        for font in self._other_copies(page, own.font):
            pool_has_them_all = every_letter is not None and letters.keys() >= every_letter
            if pool_has_them_all:
                return
            copy = self._opened(font)
            if isinstance(copy, FontCopy):
                drawn.update(copy.widths)
                yield copy
        self.cache.keep_every_letter(copies_name, frozenset(drawn))

    def shown_name(self, span: Span) -> str:
        page_font = self._page_font(span)
        if page_font is not None and page_font.kind == "type3":
            return self._copies_name(page_font)
        return strip_subset(span.font)

    def look_alike(self, span: Span) -> LookAlike:
        page_font = self._page_font(span)
        font_object = None if page_font is None else page_font.font_object
        key = (font_object, span.font)
        return self.cache.look_alike(key, partial(self._look_alike_of, span, font_object))

    def _look_alike_of(self, span: Span, font_object: int | None) -> LookAlike:
        descriptor = None if font_object is None else self.driver.font_descriptor(font_object)
        own = self.own(span)
        file_kind = None if own is None else kind_in_file(own.own.embedded.file)
        return look_alike(span.font, descriptor, file_kind=file_kind)

    def substitute(self, span: Span, text: str) -> Substitute:
        return substitute_for(self.look_alike(span).face, text)

    def drawn_codes(self, span: Span, own: PooledFont) -> dict[str, FontCode]:
        """Read from the page, so a redraw picks the shape the line shows (a tabular digit)."""
        coded = own.own.embedded.coded
        if coded is None or not coded.has_alternates(span.text):
            return {}
        by_shape = {(code.letter, code.glyph): code for code in coded.codes}
        glyphs = self.cache.page_glyphs(span.page, partial(self.driver.drawn_glyphs, span.page))
        codes: dict[str, FontCode] = {}
        for glyph in glyphs.inside(span.bbox):
            code = by_shape.get((glyph.letter, glyph.glyph))
            if code is not None:
                codes.setdefault(glyph.letter, code)
        return codes

    def letter_spacing(self, span: Span, own: PooledFont | None) -> float:
        """Kept only where the widths and the page's boxes agree, and past kerning."""
        if not span.fragments or span.turned:
            return 0.0
        glyphs = self.cache.page_glyphs(span.page, partial(self.driver.drawn_glyphs, span.page))
        # In the order drawn: a line drawn twice over, as for a shadow, starts again.
        pairs = [(a, b) for a, b in pairwise(glyphs.inside(span.bbox)) if _within_a_word(a, b)]
        if not pairs:
            return 0.0
        width_of = partial(_DrawnWidths.of(own).width, size=span.size)
        by_widths = median(
            (b.origin[0] - a.origin[0] - width_of(a)) / span.size for a, b in pairs
        )
        by_boxes = median((b.origin[0] - a.origin[0] - a.width) / span.size for a, b in pairs)
        spaced = abs(by_widths) >= constants.LETTER_SPACING_MIN_EM
        # Narrowed, not spaced: each letter's box is as narrow as it's set apart.
        agreed = abs(by_widths - by_boxes) < constants.LETTER_SPACING_MIN_EM
        return by_widths if spaced and agreed else 0.0

    def usual_gap(self, span: Span, font: FontProgram) -> float:
        measure = partial(self._usual_gap_on, span, font=font)
        key = (span.font_object, span.font, font)
        return self._facts(span.page).usual_gap(key, measure)

    def _usual_gap_on(self, span: Span, *, font: FontProgram) -> float:
        lines = self._page_lines(span.page)
        in_font = [piece for piece in _each_piece(lines) if same_font(piece, span)]
        return usual_gap(in_font, font=font)

    def _page_lines(self, page: int) -> list[list[TextPiece]]:
        return self.cache.text_lines(page, partial(self.driver.text_lines, page))


@dataclass(frozen=True, slots=True)
class _DrawnWidths:
    by_glyph: dict[int, float]
    by_letter: dict[str, float]

    @classmethod
    def of(cls, own: PooledFont | None) -> _DrawnWidths:
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
        listed = self.by_glyph.get(glyph.glyph, self.by_letter.get(glyph.letter))
        return glyph.width if listed is None else listed * size / EM


def _within_a_word(letter: DrawnGlyph, following: DrawnGlyph) -> bool:
    drawn = letter.glyph != _LIGATURE_TAIL and following.glyph != _LIGATURE_TAIL
    letters = not letter.letter.isspace() and not following.letter.isspace()
    level = abs(letter.origin[1] - following.origin[1]) <= constants.BASELINE_EPS
    onward = following.origin[0] > letter.origin[0]
    return drawn and letters and level and onward


def _each_piece(lines: list[list[TextPiece]]) -> Iterator[TextPiece]:
    for line in lines:
        yield from line
