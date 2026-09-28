"""A PDF open for editing: its spans, what we can promise about each, and the edits on it.

The product's own logic, written against a `core.driver.PdfDriver`'s primitives,
so it's the same over any PDF library. Open one with `core.open_pdf`.

The engine speaks only in primitives (remove, draw), so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from squidpdf.core import faces
from squidpdf.core.driver import FontProgram, PdfDriver
from squidpdf.core.embedded import EmbeddedFont, FontUnusable, open_embedded
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import face_bytes, look_alike, strip_subset, trimmed
from squidpdf.core.message import Message
from squidpdf.core.spans import build_index
from squidpdf.core.types import (
    CodedFont,
    Face,
    LookAlike,
    Page,
    PageFont,
    Rect,
    Span,
    SpanIndex,
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
_WORDS_AND_SPACES = re.compile(r" +|[^ ]+")  # a line cut into its words and the spaces between


def letter_widths(font: FontProgram, letters: Iterable[str]) -> dict[str, float]:
    """Each letter's width in `font`, per 1000 em, as the browser gets it."""
    return {ch: round(font.advance(ch) * _EM, _WIDTH_DP) for ch in letters}


@dataclass(frozen=True, slots=True)
class Word:
    """One word of a line, and how far from the line's start it begins, in points."""

    offset: float
    text: str


def space_widths(span: Span) -> list[float]:
    """How far the file moved the pen for each space in the span's text, in ems.

    MuPDF reads each gap back as a piece of spaces, whose box is the gap.
    """
    widths: list[float] = []
    for fragment in span.fragments:
        spaces = len(fragment.text)
        # A piece of letters, not a gap.
        if not spaces or fragment.text.strip(" "):
            continue
        widths += [fragment.bbox.width / spaces / span.size] * spaces
    return widths


def usual_space(span: Span, font: FontProgram) -> float:
    """A space the span didn't have, in ems: its median gap, else the library's figure."""
    drawn = space_widths(span)
    return statistics.median(drawn) if drawn else font.advance(" ")


def placed_words(
    text: str, *, font: FontProgram, size: float, spaces: Sequence[float], usual: float
) -> tuple[list[Word], float]:
    """Each word of `text` where the pen reaches it, and where the pen ends.

    A space draws nothing: the k-th moves the pen `spaces[k]` ems, any past those `usual`.
    """
    words: list[Word] = []
    pen = 0.0
    spaces_passed = 0
    for piece in _WORDS_AND_SPACES.findall(text):
        # A word goes where the pen is.
        if not piece.startswith(" "):
            words.append(Word(pen, piece))
            pen += font.width(piece, size)
            continue
        # Spaces move the pen.
        for k in range(spaces_passed, spaces_passed + len(piece)):
            pen += (spaces[k] if k < len(spaces) else usual) * size
        spaces_passed += len(piece)
    return words, pen


@dataclass(slots=True)
class FontCache:
    """What the engine has looked up about the fonts on each page.

    Keyed by (page, font name, subset prefix aside); each entry fills in on
    first lookup and is never recomputed.
    """

    embedded: dict[tuple[int, str], EmbeddedFont | FontUnusable] = field(default_factory=dict)
    look_alikes: dict[tuple[int, str], LookAlike] = field(default_factory=dict)
    # The page's name for each font, once drawn.
    aliases: dict[tuple[int, str], str | FontUnusable] = field(default_factory=dict)
    # The page's name for each face we ship, by (page, face file), once drawn.
    face_aliases: dict[tuple[int, str], str] = field(default_factory=dict)


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

        Exact only if the span's own font draws its own text: `draw` swaps the
        run otherwise, so a redraw of it would be in the substitute.
        """
        return [self._assess_one(span) for span in index]

    def _assess_one(self, span: Span) -> FidelityReport:
        """Exact or substitute, for one span."""
        in_file = self._embedded(span) is not None
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

        The same font `measure` uses, so widths agree. A trimmed (subset) font's
        emptied letters don't count; if Coverage can't read the font, the
        library's own list stands in. A font not in the file is drawn in its
        look-alike, whose list is kept to GLYPH_LIST_RANGES.
        """
        embedded = self._embedded(span)

        # Not in the file: the look-alike draws it.
        if embedded is None:
            face = self._look_alike(span).face
            return letter_widths(self._driver.face_font(face), faces.face_letters(face))

        letters = embedded.coverage.drawable()

        # Written by code: widths come from the font's width list.
        if embedded.coded is not None:
            codes = embedded.coded.letters
            return {ch: round(codes[ch].width, _WIDTH_DP) for ch in letters}

        # Written by letter: widths come from the font itself.
        widths = letter_widths(embedded.program, letters)
        # No space glyph: a space is the span's usual gap, as in `measure`.
        if not embedded.program.maps(" "):
            widths[" "] = round(usual_space(span, embedded.program) * _EM, _WIDTH_DP)
        return widths

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, placed as `draw` places it, at this span's size."""
        coded = self._coded_for(span, text)
        if coded is not None:
            return sum(coded.letters[ch].width for ch in text) * span.size / _EM
        font, text = self._run(span, text)
        _words, width = self._words(span, text, font=font, size=span.size)
        return width

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's font cannot actually draw.

        Checks each letter draws a shape rather than trusting the font's list: a
        trimmed (subset) font still lists letters whose shapes were emptied. A
        font not in the file is checked against its look-alike, the real file we ship.
        """
        embedded = self._embedded(span)
        # Not in the file: the look-alike draws it.
        if embedded is None:
            return faces.face_coverage(self._look_alike(span).face).missing(text)
        return embedded.coverage.missing(text)

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
        page no longer uses, and `draw` still needs both.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
            self._embedded(span)
            self._look_alike(span)

        for page, page_spans in by_page.items():
            self._driver.erase_text(page, [span.bbox for span in page_spans])

    def draw(
        self, span: Span, text: str, *, size: float | None = None, scale_x: float = 1.0
    ) -> list[Message]:
        """Redraw `text` at the span's baseline, in its own font where the file has it.

        `size` in points replaces the span's own; `scale_x` narrows the run from
        its start. A character the font can't draw sends the whole run to the
        stand-in, so a line never mixes two faces. A font with no space is drawn
        word by word. Returns anything that came out other than asked, for the
        edge to put into words; empty when nothing did.
        """
        font_size = span.size if size is None else size

        # A font written by code gets codes, as the original did.
        coded = self._coded_for(span, text)
        if coded is not None:
            self._draw_codes(span, coded, text=text, size=font_size, scale_x=scale_x)
            return []

        # Written by letter, in the file's own font when it has every letter.
        notices: list[Message] = []
        embedded = self._embedded(span)
        if embedded is not None and not self.missing(span, text):
            try:
                alias = self._alias(span, embedded)
            except FontUnusable as problem:  # the page wouldn't take the font
                notices.append(problem.reason)
            else:
                self._write(
                    span,
                    text,
                    font=embedded.program,
                    alias=alias,
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
        face = self._driver.face_font(drawn_in.face)
        self._write(
            span, drawn_in.text, font=face, alias=alias, size=font_size, scale_x=scale_x
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

    # The span's own font, and what stands in for it.

    def _lookup(self, span: Span) -> EmbeddedFont | FontUnusable:
        """The file's own copy of the span's font, or why we can't use it."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.embedded:
            try:
                self._cache.embedded[key] = self._load_font(span.page, font_name)
            except FontUnusable as problem:  # no copy of the font we can use, and why
                self._cache.embedded[key] = problem
        return self._cache.embedded[key]

    def _embedded(self, span: Span) -> EmbeddedFont | None:
        """The file's own copy of the span's font, or None when we can't use it.

        Only named, not stored, is the common case for anything exported from
        Word, and it is exactly what the substitute state warns about.
        """
        found = self._lookup(span)
        return found if isinstance(found, EmbeddedFont) else None

    def _why_not(self, span: Span) -> Message | None:
        """Why the span's own font can't be used; None when it can."""
        found = self._lookup(span)
        return found.reason if isinstance(found, FontUnusable) else None

    def _load_font(self, page: int, font_name: str) -> EmbeddedFont:
        """Find the font on the page and open the file's copy of it.

        Raises FontUnusable, saying why, when there's no copy we can use.
        """
        page_font = self._page_font(page, font_name)
        # Not on the page: new text in a font it doesn't have.
        if page_font is None:
            raise FontUnusable(Message("font_not_in_file"))
        return open_embedded(self._driver, page_font)

    def _page_font(self, page: int, font_name: str) -> PageFont | None:
        """The page's font by this name, subset prefix aside; None when the page has none.

        The first by this name, in the library's order; a later one is never tried.
        """
        return next(
            (font for font in self._driver.fonts(page) if strip_subset(font.name) == font_name),
            None,
        )

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

        The span's own font if it draws every character; otherwise the whole run
        in the stand-in, less what even that can't draw.
        """
        embedded = self._embedded(span)
        if embedded is not None and not self.missing(span, text):
            return embedded.program, text
        drawn_in = self._stand_in(span, text)
        return self._driver.face_font(drawn_in.face), drawn_in.text

    # Drawing.

    def _words(
        self, span: Span, text: str, *, font: FontProgram, size: float
    ) -> tuple[list[Word], float]:
        """`text` as `draw` places it in `font`, and its width.

        A font with no space would draw its empty glyph for one, so its words go one by one.
        """
        # It has a space, or needs none: one run.
        if font.maps(" ") or " " not in text:
            return [Word(0.0, text)], font.width(text, size)
        spaces, usual = space_widths(span), usual_space(span, font)
        return placed_words(text, font=font, size=size, spaces=spaces, usual=usual)

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        alias: str,
        size: float,
        scale_x: float,
    ) -> None:
        """Write `text` at the span's baseline in `font`, which the page calls `alias`."""
        x, y = span.origin
        words, _width = self._words(span, text, font=font, size=size)
        runs = [TextRun(word.text, (x + word.offset * scale_x, y)) for word in words]
        self._driver.write_text(
            span.page, runs=runs, font=alias, size=size, color=span.color, scale_x=scale_x
        )

    def _coded_for(self, span: Span, text: str) -> CodedFont | None:
        """The span's font drawn by code, if it has one and it can draw all of `text`."""
        embedded = self._embedded(span)
        if embedded is None or embedded.coded is None or self.missing(span, text):
            return None
        return embedded.coded

    def _draw_codes(
        self, span: Span, coded: CodedFont, *, text: str, size: float, scale_x: float
    ) -> None:
        """Write `text` as codes in the file's own font, on top of the page."""
        self._driver.restore_font(span.page, coded.resource, coded.xref)
        x, y = self._driver.to_pdf_space(span.page, span.origin)
        r, g, b = span.color
        hex_digits = coded.code_bytes * 2
        hex_codes = "".join(f"{coded.letters[ch].value:0{hex_digits}x}" for ch in text)
        # Save the page's settings, set color, font and size, place the text,
        # write the codes, then put the settings back.
        stream = (
            f"q BT {r:.{_PDF_DP}f} {g:.{_PDF_DP}f} {b:.{_PDF_DP}f} rg"
            f" /{coded.resource} {size:.{_PDF_DP}f} Tf"
            f" {scale_x:.{_PDF_DP}f} 0 0 1 {x:.{_PDF_DP}f} {y:.{_PDF_DP}f} Tm"
            f" <{hex_codes}> Tj ET Q"
        )
        self._driver.add_content(span.page, stream.encode())

    def _alias(self, span: Span, embedded: EmbeddedFont) -> str:
        """The page's name for this span's font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises FontUnusable when the library won't add it.
        """
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._cache.aliases:
            self._cache.aliases[key] = self._add_font(span.page, font_name, embedded)
        found = self._cache.aliases[key]
        if isinstance(found, FontUnusable):
            raise FontUnusable(found.reason)
        return found

    def _add_font(
        self, page: int, font_name: str, embedded: EmbeddedFont
    ) -> str | FontUnusable:
        """Add the file's copy of a font to a page under a new name, or say why it failed."""
        # Named from the font, so it can't clash with a name already on the page.
        digest = hashlib.blake2s(font_name.encode(), digest_size=_ALIAS_DIGEST_SIZE)
        alias = "F" + digest.hexdigest()
        try:
            self._driver.add_font(page, alias, embedded.file)
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
            # From the file's name, so it can't clash with the page's own names.
            digest = hashlib.blake2s(face.file.encode(), digest_size=_ALIAS_DIGEST_SIZE)
            alias = "S" + digest.hexdigest()
            xref = self._driver.add_font(page, alias, face_bytes(face))
            self._cache.face_aliases[key] = alias
            self._added.by_xref[xref] = face
        return self._cache.face_aliases[key]


def unspaced(text: str) -> str:
    """`text` with every space, tab and line break taken out."""
    return "".join(text.split())
