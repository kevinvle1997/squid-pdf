"""A PDF open for editing: its spans, what we can promise about each, and the edits on it.

The product's own logic, written against a `core.driver.PdfDriver`'s primitives,
so it's the same over any PDF library. Open one with `core.open_pdf`.

The engine speaks only in primitives (remove, draw), so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from itertools import groupby

from squidpdf.core import faces
from squidpdf.core.driver import FontProgram, PdfDriver
from squidpdf.core.embedded import FontUnusable, open_embedded
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import face_bytes, look_alike, strip_subset, trimmed
from squidpdf.core.message import Message
from squidpdf.core.pooled import FontCopy, PooledFont, font_copy, pooled
from squidpdf.core.spacing import Word, lacks_space, placed_words, span_gaps, usual_gap
from squidpdf.core.spans import build_index
from squidpdf.core.types import (
    SOLID,
    CodedFont,
    Face,
    LookAlike,
    Page,
    PageFont,
    Rect,
    Span,
    SpanIndex,
    TextPiece,
    TextRun,
)

__all__ = [
    "letter_widths",
    "FontCache",
    "AddedFaces",
    "Engine",
]

_EM = 1000  # widths are given per 1000 em, as PDF font widths are
_WIDTH_DP = 2  # finer than any page can show
_ALIAS_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids
_PDF_DP = 4  # decimals written into a content stream, far below a device pixel


def letter_widths(font: FontProgram, letters: Iterable[str]) -> dict[str, float]:
    """Each letter's width in `font`, per 1000 em, as the browser gets it."""
    return {ch: round(font.advance(ch) * _EM, _WIDTH_DP) for ch in letters}


@dataclass(slots=True)
class FontCache:
    """What the engine has looked up about the file: its fonts, and each page's text as it was.

    Each entry fills in on first lookup and is never recomputed.
    """

    # Every page's fonts, read once, before an edit can drop one: None until asked for.
    page_fonts: list[list[PageFont]] | None = None
    # Each copy of a font, opened, or why it can't be used, by the page's entry for it.
    copies: dict[PageFont, FontCopy | FontUnusable] = field(default_factory=dict)
    # A span's font with its other copies, by (page, font name, subset prefix aside).
    pools: dict[tuple[int, str], PooledFont | FontUnusable] = field(default_factory=dict)
    look_alikes: dict[tuple[int, str], LookAlike] = field(default_factory=dict)
    # The page's name for each copy of a font, once drawn, by (page, font object).
    aliases: dict[tuple[int, int], str | FontUnusable] = field(default_factory=dict)
    # The page's name for each face we ship, by (page, face file), once drawn.
    face_aliases: dict[tuple[int, str], str] = field(default_factory=dict)
    # The page's usual gap for a space, in a font with none of its own.
    usual_gaps: dict[tuple[int, str], float] = field(default_factory=dict)
    # Each page's text as it was, read once.
    lines: dict[int, list[list[TextPiece]]] = field(default_factory=dict)


@dataclass(slots=True)
class AddedFaces:
    """The faces we ship that were added to the document, for `save` to trim."""

    # By font object: pages share one per face.
    by_xref: dict[int, Face] = field(default_factory=dict)
    # Every letter drawn in each face, over all pages.
    drawn: dict[Face, set[str]] = field(default_factory=dict)


class Engine:
    """A PDF open for editing. Use it in a `with`, or close it."""

    def __init__(self, driver: PdfDriver) -> None:
        """Take over an open document, with empty per-font caches."""
        self._driver = driver
        self._cache = FontCache()
        self._added = AddedFaces()

    # What the document says.

    def index(self) -> SpanIndex:
        """Every editable span, extracted once from the pristine document."""
        page_count = len(self._driver.pages())
        return build_index(self._driver.text_lines(page) for page in range(page_count))

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        return self._driver.pages()

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box.

        No alpha channel: the page is white whatever the app's theme (Rule 2).
        Unrotated, so the image lines up with the span boxes; the browser turns it.
        """
        return self._driver.page_image(page, scale, clip)

    # What we can promise about it.

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Judge every span in the index as exact or substitute.

        Exact only if the file's copies of the span's font draw its text: `draw`
        swaps the run otherwise, so a redraw of it would be in the substitute.
        """
        return [self._assess_one(span) for span in index]

    def _assess_one(self, span: Span) -> FidelityReport:
        """Exact or substitute, for one span."""
        in_file = self._pool(span) is not None
        exact = in_file and not self.missing(span, span.text)
        if exact:
            return FidelityReport(span.id, Fidelity.EXACT, span.font)
        match = self._look_alike(span)
        drawn_in = self._stand_in(span, span.text)
        return FidelityReport(
            span.id,
            Fidelity.SUBSTITUTE,
            span.font,
            substitute=drawn_in.face.name,
            why=self._why_not(span) if not in_file else Message("font_lacks_letters"),
            same_widths=match.same_widths and drawn_in.face == match.face,
        )

    def widths(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em.

        The same font `measure` uses, so widths agree: every copy of it in the
        file, pooled. A trimmed (subset) font's emptied letters don't count; if
        Coverage can't read a copy, the library's own list stands in. A font not
        in the file is drawn in its look-alike, whose list is kept to
        GLYPH_LIST_RANGES.
        """
        pool = self._pool(span)

        # Not in the file: the look-alike draws it.
        if pool is None:
            face = self._look_alike(span).face
            return letter_widths(self._driver.face_font(face), faces.face_letters(face))

        # Each from the copy that draws it: its width list if written by code, else the font.
        letters = sorted(pool.letters.items())
        widths = {ch: round(copy.widths[ch], _WIDTH_DP) for ch, copy in letters}
        # Written by letter, with no space of its own: a space is the page's usual gap.
        spaceless = pool.own.embedded.coded is None and lacks_space(pool)
        if spaceless:
            widths[" "] = round(self._usual_gap(span, pool) * _EM, _WIDTH_DP)
        return widths

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, placed as `draw` places it, at this span's size."""
        by_code = self._coded_for(span, text)
        if by_code is not None:
            return sum(by_code.letters[ch].widths[ch] for ch in text) * span.size / _EM
        font, text = self._run(span, text)
        _words, width = self._words(span, text, font=font, size=span.size)
        return width

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters no copy of this span's font in the file can actually draw.

        Checks each letter draws a shape rather than trusting the font's list: a
        trimmed (subset) font still lists letters whose shapes were emptied. A
        font not in the file is checked against its look-alike, the real file we ship.
        """
        pool = self._pool(span)
        # Not in the file: the look-alike draws it.
        if pool is None:
            return faces.face_coverage(self._look_alike(span).face).missing(text)
        return pool.missing(text)

    def left_out(self, span: Span, text: str) -> list[str]:
        """Characters no font we have can draw here, so a redraw leaves them out."""
        if not self.missing(span, text):
            return []
        return self._stand_in(span, text).left_out

    def stand_in(self, span: Span, text: str) -> str:
        """The face we ship that draws `text` when the span's own font can't: "Carlito Bold"."""
        return self._stand_in(span, text).face.name

    # Changing it.

    def remove(self, spans: list[Span]) -> None:
        """Delete these spans' text for real, not by covering it with a box.

        One box per span, not per fragment: the cost grows with the box count,
        and a span's box covers its fragments. Lines and underlines stay. Each
        span's font and look-alike are read first: erasing can delete a font the
        page no longer uses, and `draw` still needs both. So are the page's gaps.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
            pool = self._pool(span)
            self._look_alike(span)
            # Erasing takes the gaps a new space is measured against.
            if pool is not None and lacks_space(pool):
                self._usual_gap(span, pool)

        for page, page_spans in by_page.items():
            self._driver.erase_text(page, [span.bbox for span in page_spans])

    def draw(
        self, span: Span, text: str, *, size: float | None = None, scale_x: float = 1.0
    ) -> list[Message]:
        """Redraw `text` at the span's baseline, in its own font where the file has it.

        `size` in points replaces the span's own; `scale_x` narrows the run from
        its start. A letter the span's copy of its font lacks comes from another
        copy of the same font in the file, placed as one font would place it. A
        character no copy can draw sends the whole run to the stand-in, so a
        line never mixes two faces. A font with no space is drawn word by word.
        Returns anything that came out other than asked, for the edge to put
        into words; empty when nothing did.
        """
        font_size = span.size if size is None else size

        # A font written by code gets codes, as the original did.
        by_code = self._coded_for(span, text)
        if by_code is not None:
            self._draw_codes(span, by_code, text=text, size=font_size, scale_x=scale_x)
            return []

        # Written by letter, in the file's own copies when they have every letter.
        notices: list[Message] = []
        pool = self._pool(span)
        if pool is not None and not pool.missing(text):
            try:
                aliases = self._aliases(span.page, pool, text)
            except FontUnusable as problem:  # the page wouldn't take a copy of the font
                notices.append(problem.reason)
            else:
                self._write(
                    span,
                    text,
                    font=pool,
                    aliases=aliases,
                    size=font_size,
                    scale_x=scale_x,
                )
                return notices

        # Otherwise the stand-in draws the whole run, less what even it can't draw.
        drawn_in = self._stand_in(span, text)
        if drawn_in.left_out:
            notices.append(Message("left_out", {"letters": list(drawn_in.left_out)}))
        alias = self._face_alias(span.page, drawn_in.face)
        self._added.drawn.setdefault(drawn_in.face, set()).update(drawn_in.text)
        self._write(
            span,
            drawn_in.text,
            font=self._driver.face_font(drawn_in.face),
            aliases=dict.fromkeys(drawn_in.text, alias),
            size=font_size,
            scale_x=scale_x,
        )
        return notices

    def keep_pages(self, pages: list[int]) -> list[Message]:
        """Keep only `pages`, in that order: page `pages[0]` becomes the first.

        Call it after the last draw: page numbers change here. Returns what
        came out other than asked.
        """
        every_page_kept = set(pages) == set(range(len(self._driver.pages())))
        said = [] if every_page_kept else self._drop_tags()
        self._driver.keep_pages(pages)
        self._cache = FontCache()  # looked up by page number, and those just changed
        return said

    def _drop_tags(self) -> list[Message]:
        """Drop the file's tags, and say so if it had any.

        They point at every page, so they would keep left-out pages in the file.
        """
        if not self._driver.has_tags():
            return []
        self._driver.drop_tags()
        return [Message("tags_dropped")]

    def save(self, path: str) -> list[Message]:
        """Write the document to `path`, our faces cut to the letters drawn in them.

        Call it last: afterwards our faces can't draw any new letter. Returns
        anything that came out other than asked, for the edge to put into words.
        """
        notices: list[Message] = []
        for xref, face in self._added.by_xref.items():
            try:
                font_file = trimmed(face, self._added.drawn[face])
            except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font)
                # The whole file still draws every letter; the file is only bigger.
                font_file = face_bytes(face)
                notices.append(Message("face_not_trimmed", {"font": face.name}))
            self._driver.replace_font_file(xref, font_file)
        self._driver.save(path)
        return notices

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans whose text is still in their box. A black box over it doesn't hide it.

        Only each span's own box is read, so the same words elsewhere aren't a
        leak. Spaces are ignored, so respacing can't hide a leftover.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
        left = (self._left_on(page, on_page) for page, on_page in by_page.items())
        return [span for on_page in left for span in on_page]

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, whose text is still in their box."""
        texts = self._driver.text_in(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if unspaced(span.text) in unspaced(left)]

    def close(self) -> None:
        """Release the open document."""
        self._driver.close()

    def __enter__(self) -> Engine:
        """Lets the engine be used as `with open_pdf(path) as engine:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the document when the `with` block ends."""
        self.close()

    # The span's own font, its other copies, and what stands in for them.

    def _lookup(self, span: Span) -> PooledFont | FontUnusable:
        """The span's font, pooled with its other copies in the file, or why we can't use it."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.pools:
            try:
                self._cache.pools[key] = self._load_pool(span.page, font_name)
            except FontUnusable as problem:  # no copy of the font we can use, and why
                self._cache.pools[key] = problem
        return self._cache.pools[key]

    def _pool(self, span: Span) -> PooledFont | None:
        """The span's font, pooled with its other copies in the file; None when we can't use it.

        Only named, not stored, is the common case for anything exported from
        Word, and it is exactly what the substitute state warns about.
        """
        found = self._lookup(span)
        return found if isinstance(found, PooledFont) else None

    def _why_not(self, span: Span) -> Message | None:
        """Why the span's own font can't be used; None when it can."""
        found = self._lookup(span)
        return found.reason if isinstance(found, FontUnusable) else None

    def _load_pool(self, page: int, font_name: str) -> PooledFont:
        """Open the page's copy of the font, then pool the file's other copies with it.

        Raises FontUnusable, saying why, when the page has no copy we can use.
        """
        page_font = self._page_font(page, font_name)
        # Not on the page: new text in a font it doesn't have.
        if page_font is None:
            raise FontUnusable(Message("font_not_in_file"))
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
        del copies[own.xref]
        return list(copies.values())

    def _page_font(self, page: int, font_name: str) -> PageFont | None:
        """The page's font by this name, subset prefix aside; None when the page has none.

        The first by this name, in the library's order; any other is pooled with it.
        """
        return next(
            (font for font in self._page_fonts()[page] if strip_subset(font.name) == font_name),
            None,
        )

    def _page_fonts(self) -> list[list[PageFont]]:
        """Every page's fonts, read once, before an edit can drop one."""
        if self._cache.page_fonts is None:
            page_count = len(self._driver.pages())
            self._cache.page_fonts = [self._driver.fonts(page) for page in range(page_count)]
        return self._cache.page_fonts

    def _look_alike(self, span: Span) -> LookAlike:
        """The face we ship that stands in for the span's font, in its style."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.look_alikes:
            page_font = self._page_font(span.page, font_name)
            # New text, or a font the page doesn't list: go by the name alone.
            descriptor = (
                None if page_font is None else self._driver.font_descriptor(page_font.xref)
            )
            self._cache.look_alikes[key] = look_alike(span.font, descriptor)
        return self._cache.look_alikes[key]

    def _stand_in(self, span: Span, text: str) -> faces.StandIn:
        """The face that draws `text` when the span's own font can't, and what it leaves out."""
        return faces.stand_in(self._look_alike(span).face, text)

    def _run(self, span: Span, text: str) -> tuple[FontProgram, str]:
        """The font `text` is drawn in, and the text as it can be drawn.

        The file's copies of the span's font if they draw every character;
        otherwise the whole run in the stand-in, less what even that can't draw.
        """
        pool = self._pool(span)
        if pool is not None and not pool.missing(text):
            return pool, text
        drawn_in = self._stand_in(span, text)
        return self._driver.face_font(drawn_in.face), drawn_in.text

    # Drawing.

    def _words(
        self, span: Span, text: str, *, font: FontProgram, size: float
    ) -> tuple[list[Word], float]:
        """`text` as `draw` places it in `font`, and where the pen ends: its width."""
        one_run = not lacks_space(font) or " " not in text
        if one_run:
            return [Word(text, 0.0)], font.width(text, size)
        # No space to draw: each word goes where the file's gaps put it.
        gaps, usual = span_gaps(span, font), self._usual_gap(span, font)
        return placed_words(text, font=font, size=size, gaps=gaps, usual=usual)

    def _usual_gap(self, span: Span, font: FontProgram) -> float:
        """The page's usual gap for a space in the span's font, read once, before any edit."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.usual_gaps:
            lines = self._page_lines(span.page)
            self._cache.usual_gaps[key] = usual_gap(lines, font_name=font_name, font=font)
        return self._cache.usual_gaps[key]

    def _page_lines(self, page: int) -> list[list[TextPiece]]:
        """The page's text as it was when first asked for, line by line."""
        if page not in self._cache.lines:
            self._cache.lines[page] = self._driver.text_lines(page)
        return self._cache.lines[page]

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        aliases: Mapping[str, str],
        size: float,
        scale_x: float,
    ) -> None:
        """Write `text` at the span's baseline, placed by `font`'s widths.

        `aliases` is the page's name for the font each letter is drawn in. A
        run per stretch in one font, each where one font would have put it, in
        reading order, so the text reads back as written.
        """
        x, y = span.origin
        words, _width = self._words(span, text, font=font, size=size)
        stretches = each_stretch(words, aliases=aliases, font=font, size=size)
        runs = [
            TextRun(stretch.text, (x + stretch.offset * scale_x, y), alias)
            for alias, stretch in stretches
        ]
        self._driver.write_text(
            span.page,
            runs=runs,
            size=size,
            color=span.color,
            opacity=span.opacity,
            scale_x=scale_x,
        )

    def _coded_for(self, span: Span, text: str) -> PooledFont | None:
        """The span's font pooled, if it's written by code and can draw all of `text`."""
        pool = self._pool(span)
        if pool is None or pool.own.embedded.coded is None or pool.missing(text):
            return None
        return pool

    def _draw_codes(
        self, span: Span, pool: PooledFont, *, text: str, size: float, scale_x: float
    ) -> None:
        """Write `text` as codes in the file's own copies of its font, on top of the page.

        One text object: each stretch switches to its copy, and the pen moves on
        by that copy's widths, which agree with the others'.
        """
        x, y = self._driver.to_pdf_space(span.page, span.origin)
        r, g, b = span.color
        shown: list[str] = []
        for copy, stretch in pool.stretches(text):
            resource = self._resource_for(span.page, pool, copy)
            codes = hex_codes(coded_of(copy), stretch)
            shown.append(f"/{resource} {size:.{_PDF_DP}f} Tf <{codes}> Tj")
        # See-through, as the original was.
        paint = ""
        if span.opacity < SOLID:
            paint = f" /{self._driver.add_opacity(span.page, span.opacity)} gs"
        # Save the page's settings, set color, place the text, write each stretch
        # in its font and size, then put the settings back.
        stream = (
            f"q{paint} BT {r:.{_PDF_DP}f} {g:.{_PDF_DP}f} {b:.{_PDF_DP}f} rg"
            f" {scale_x:.{_PDF_DP}f} 0 0 1 {x:.{_PDF_DP}f} {y:.{_PDF_DP}f} Tm"
            f" {' '.join(shown)} ET Q"
        )
        self._driver.add_content(span.page, stream.encode())

    def _resource_for(self, page: int, pool: PooledFont, copy: FontCopy) -> str:
        """The page's name for a copy written by code, pointed at it before each draw.

        Erasing can drop a font the page no longer uses. The span's own copy
        keeps the name the page gave it; any other gets a fresh one, since
        another page's name for it may mean something else here.
        """
        own = copy.font.xref == pool.own.font.xref
        fresh = page_name("C", f"{copy.font.xref} {copy.font.name}")
        resource = coded_of(copy).resource if own else fresh
        self._driver.restore_font(page, resource, copy.font.xref)
        return resource

    def _aliases(self, page: int, pool: PooledFont, text: str) -> dict[str, str]:
        """The page's name for the copy each letter of `text` is drawn in.

        Raises FontUnusable when the library won't add one of them.
        """
        return {ch: self._alias(page, pool.copy_for(ch)) for ch in dict.fromkeys(text)}

    def _alias(self, page: int, copy: FontCopy) -> str:
        """The page's name for a copy of a font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises FontUnusable when the library won't add it.
        """
        key = (page, copy.font.xref)
        if key not in self._cache.aliases:
            self._cache.aliases[key] = self._add_font(page, copy)
        found = self._cache.aliases[key]
        if isinstance(found, FontUnusable):
            raise FontUnusable(found.reason)
        return found

    def _add_font(self, page: int, copy: FontCopy) -> str | FontUnusable:
        """Add a copy of a font in the file to a page under a new name, or say why it failed."""
        # Each copy its own name, by its object: copies of one font share their name.
        alias = page_name("F", f"{copy.font.xref} {copy.font.name}")
        try:
            self._driver.add_font(page, alias, copy.embedded.file)
        except ValueError:
            # The bytes opened as a font, but adding them to a page is another path
            # that can still fail; the stand-in draws instead.
            return FontUnusable(Message("font_not_added"))
        return alias

    def _face_alias(self, page: int, face: Face) -> str:
        """The page's name for a face we ship, added to the page on first use.

        Once per page, as `_alias` does for the file's own fonts.
        """
        key = (page, face.file)
        if key not in self._cache.face_aliases:
            alias = page_name("S", face.file)
            xref = self._driver.add_font(page, alias, face_bytes(face))
            self._cache.face_aliases[key] = alias
            self._added.by_xref[xref] = face
        return self._cache.face_aliases[key]


def page_name(kind: str, source: str) -> str:
    """A new name for the page's resources, `kind` then a digest of `source`.

    Made from what it names, so it can't clash with a name already on the page.
    """
    digest = hashlib.blake2s(source.encode(), digest_size=_ALIAS_DIGEST_SIZE)
    return kind + digest.hexdigest()


def each_stretch(
    words: Iterable[Word], *, aliases: Mapping[str, str], font: FontProgram, size: float
) -> Iterator[tuple[str, Word]]:
    """Each stretch of a word in one font: the page's name for it, and where it starts."""
    for word in words:
        pen = word.offset
        for alias, letters in groupby(word.text, key=aliases.__getitem__):
            stretch = "".join(letters)
            yield alias, Word(stretch, pen)
            pen += font.width(stretch, size)


def coded_of(copy: FontCopy) -> CodedFont:
    """The codes a copy is written in. Every copy pooled with one written by code has them."""
    coded = copy.embedded.coded
    assert coded is not None, "a pool written by code holds only copies written by code"
    return coded


def hex_codes(coded: CodedFont, text: str) -> str:
    """`text` as the font's codes, in hex, each as many bytes wide as the font's."""
    hex_digits = coded.code_bytes * 2
    return "".join(f"{coded.letters[ch].value:0{hex_digits}x}" for ch in text)


def unspaced(text: str) -> str:
    """`text` with every space, tab and line break taken out."""
    return "".join(text.split())
