"""PyMuPDF implementation of the engine.

Two things here are not obvious from the interface: fragments are merged into
logical spans at extraction, and the index is only ever built from the pristine
document.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from functools import cache

import pymupdf

from squidpdf.core import words
from squidpdf.core.constants import (
    BASELINE_EPS,
    GAP_RATIO,
    GLYPH_LIST_RANGES,
    LIBRARY_VERSION,
    SIZE_EPS,
)
from squidpdf.core.coverage import Coverage
from squidpdf.core.errors import Damaged, Encrypted
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import broadest, face_bytes, look_alike, strip_subset, trimmed
from squidpdf.core.pdf import PageFont, PdfFile, TextPiece
from squidpdf.core.types import (
    CodedFont,
    Face,
    FontCode,
    Fragment,
    LookAlike,
    Page,
    Rect,
    Span,
    SpanIndex,
    span_id,
)

_GARBAGE_COLLECT_MAX = 3  # PyMuPDF's highest level: dedupe + drop unused objects

_EM = 1000  # widths are given per 1000 em, as PDF font widths are
_WIDTH_DP = 2  # finer than any page can show
_POSITION_DP = 2  # boxes, origins and sizes, as span ids and the client see them

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES = {"TrueType": 1, "Type0": 2}

_ALIAS_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel

# What drew and judged a page; a new one means earlier images and fidelity may differ.
BUILD = f"mupdf-{pymupdf.mupdf_version}.fonts-{LIBRARY_VERSION}"


@dataclass(frozen=True, slots=True)
class _EmbeddedFont:
    """A font stored in the file, and what it really draws."""

    font: pymupdf.Font
    coverage: Coverage  # which letters draw a shape
    coded: CodedFont | None  # set when we write it by code, not by letter


@dataclass(frozen=True, slots=True)
class _StandIn:
    """A face we ship drawing a line the span's own font can't, less what even it can't draw."""

    face: Face
    text: str  # the line as it's drawn, without the letters left out
    left_out: list[str]  # each once, in the order typed


class _FontUnusable(Exception):
    """Why the file's own copy of a font can't be used, so a similar font draws instead."""

    def __init__(self, reason: str) -> None:
        """`reason` is a sentence from `core.words`, shown to the user as it is."""
        super().__init__(reason)
        self.reason = reason


class MuPDFEngine:
    """Implements `core.engine.Engine`."""

    def __init__(self, path: str) -> None:
        """Open the PDF at `path` and set up the empty per-font caches."""
        self.path = path
        try:
            # Only ever as a PDF: left to sniff, MuPDF opens a PNG as a document.
            self.doc = pymupdf.open(path, filetype="pdf")
        except pymupdf.FileDataError as exc:  # garbage, truncated or empty
            raise Damaged from exc
        if self.doc.needs_pass:  # it opens, but every page is locked behind a password
            self.doc.close()
            raise Encrypted
        self._pdf = PdfFile(self.doc)
        # Keyed by (page, font name); each fills in on first lookup.
        self._fonts: dict[tuple[int, str], _EmbeddedFont | _FontUnusable] = {}
        self._aliases: dict[tuple[int, str], str | _FontUnusable] = {}  # once drawn
        self._look_alikes: dict[tuple[int, str], LookAlike] = {}
        self._face_aliases: dict[tuple[int, str], str] = {}  # by (page, file), once drawn
        self._faces_added: dict[int, Face] = {}  # by font object; pages share one per face
        self._drawn: dict[Face, set[str]] = {}  # every letter drawn in each face, all pages

    def _lookup(self, span: Span) -> _EmbeddedFont | _FontUnusable:
        """The file's own copy of the span's font, or why we can't use it."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._fonts:
            try:
                self._fonts[key] = self._load_font(span.page, font_name)
            except _FontUnusable as problem:
                self._fonts[key] = problem
        return self._fonts[key]

    def _embedded(self, span: Span) -> _EmbeddedFont | None:
        """The file's own copy of the span's font, or None when we can't use it.

        Only named, not stored, is the common case for anything exported from
        Word, and it is exactly what the substitute state warns about.
        """
        found = self._lookup(span)
        return found if isinstance(found, _EmbeddedFont) else None

    def _why_not(self, span: Span) -> str | None:
        """Why the span's own font can't be used, in plain words; None when it can."""
        found = self._lookup(span)
        return found.reason if isinstance(found, _FontUnusable) else None

    def _load_font(self, page: int, font_name: str) -> _EmbeddedFont:
        """Find the font on the page and open the file's copy of it.

        Raises _FontUnusable, saying why, when there's no copy we can use.
        """
        page_font = self._page_font(page, font_name)
        # Not on the page, or only named there.
        if page_font is None or not page_font.is_embedded:
            raise _FontUnusable(words.FONT_NOT_IN_FILE)
        try:
            return _open_font(self._pdf, page_font)
        except (RuntimeError, ValueError) as exc:  # MuPDF can't open it
            raise _FontUnusable(words.FONT_UNREADABLE) from exc

    def _page_font(self, page: int, font_name: str) -> PageFont | None:
        """The page's font by this name, subset prefix aside; None when the page has none.

        The first by this name, in MuPDF's order; a later one is never tried.
        """
        return next(
            (font for font in self._pdf.fonts(page) if strip_subset(font.name) == font_name),
            None,
        )

    def _look_alike(self, span: Span) -> LookAlike:
        """The face we ship that stands in for the span's font, in its style."""
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._look_alikes:
            page_font = self._page_font(span.page, font_name)
            # New text, or a font the page doesn't list: go by the name alone.
            descriptor = (
                None if page_font is None else self._pdf.font_descriptor(page_font.xref)
            )
            self._look_alikes[key] = look_alike(span.font, descriptor)
        return self._look_alikes[key]

    def _stand_in(self, span: Span, text: str) -> _StandIn:
        """The face that draws `text` when the span's own font can't, and what it leaves out.

        The look-alike when it has every letter; otherwise whichever of it and
        the broadest face we ship leaves out fewer, the look-alike on a tie.
        One face for the whole line: two would look like a mistake.
        """
        first_choice = self._look_alike(span).face
        runs = [_stand_in_run(face, text) for face in (first_choice, broadest(first_choice))]
        return min(runs, key=lambda run: len(run.left_out))  # min keeps the first of a tie

    def index(self) -> SpanIndex:
        """Built once, from the pristine document. Never rebuilt from a patched one.

        That invariant is what keeps span ids stable: re-extracting after an edit
        would change every id and dangle every reference the client holds.
        """
        spans: list[Span] = []
        ordinal = 0

        for pno in range(len(self.doc)):
            for line in self._pdf.text_lines(pno):
                for group in _merge(line):
                    span = self._build(pno, group, ordinal)
                    if span is not None:
                        spans.append(span)
                        ordinal += 1

        return SpanIndex(spans)

    def _build(self, pno: int, group: list[TextPiece], ordinal: int) -> Span | None:
        """Turn one merged group of text pieces into a Span, or None if blank."""
        text = "".join(piece.text for piece in group)
        if not text.strip():
            return None

        fragments = tuple(
            Fragment(
                text=piece.text, bbox=_round_box(piece.box), origin=_round_point(piece.origin)
            )
            for piece in group
        )
        bbox = fragments[0].bbox
        for fragment in fragments[1:]:
            bbox = bbox.union(fragment.bbox)

        first = group[0]  # the span takes its style from its first piece
        return Span(
            id=span_id(pno, bbox, first.font, text, ordinal),
            page=pno,
            text=text,
            font=first.font,
            size=round(first.size, _POSITION_DP),
            color=first.color,
            bbox=bbox,
            origin=fragments[0].origin,
            fragments=fragments,
        )

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        pages = [self.doc[pno] for pno in range(len(self.doc))]
        return [Page(p.cropbox.width, p.cropbox.height, p.rotation) for p in pages]

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page as a PNG, `scale` pixels per point, or only the `clip` box of it.

        No alpha channel: the page is white whatever the app's theme (Rule 2).
        Unrotated, so the image lines up with the span boxes; the browser turns
        it. The clip is mapped into the rotated page, where MuPDF clips.
        """
        pg = self.doc[page]
        box = None if clip is None else pymupdf.Rect(clip.x0, clip.y0, clip.x1, clip.y1)
        pix = pg.get_pixmap(
            matrix=pg.derotation_matrix * pymupdf.Matrix(scale, scale),
            clip=None if box is None else box * pg.rotation_matrix,
            alpha=False,
        )
        return pix.tobytes("png")

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
        stand_in = self._stand_in(span, span.text)
        return FidelityReport(
            span.id,
            Fidelity.SUBSTITUTE,
            span.font,
            substitute=stand_in.face.name,
            why=self._why_not(span) if not in_file else words.FONT_LACKS_LETTERS,
            same_widths=match.same_widths and stand_in.face == match.face,
        )

    def glyphs(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em.

        The same font `measure` uses, so widths agree. A trimmed (subset) font's
        emptied letters don't count; if Coverage can't read the font, MuPDF's own
        list stands in. A font not in the file is drawn in its look-alike, whose
        list is kept to GLYPH_LIST_RANGES.
        """
        embedded = self._embedded(span)

        # Not in the file: the look-alike draws it.
        if embedded is None:
            return face_glyphs(self._look_alike(span).face)

        letters = embedded.coverage.drawable()

        # Written by code: widths come from the font's width list.
        if embedded.coded is not None:
            codes = embedded.coded.letters
            return {ch: round(codes[ch].width, _WIDTH_DP) for ch in letters}

        # Written by letter: widths come from the font itself.
        return _widths(embedded.font, letters)

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, in the face `draw` would pick and this span's size."""
        coded = self._coded_for(span, text)
        if coded is not None:
            return sum(coded.letters[ch].width for ch in text) * span.size / _EM
        font, text = self._run(span, text)
        return font.text_length(text, fontsize=span.size)

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's font cannot actually draw.

        Checks each letter draws a shape rather than trusting the font's list: a
        trimmed (subset) font still lists letters whose shapes were emptied. A
        font not in the file is checked against its look-alike, the real file we ship.
        """
        embedded = self._embedded(span)
        # Not in the file: the look-alike draws it.
        if embedded is None:
            return _face_coverage(self._look_alike(span).face).missing(text)
        return embedded.coverage.missing(text)

    def left_out(self, span: Span, text: str) -> list[str]:
        """Characters no font we have can draw here, so a redraw leaves them out."""
        if not self.missing(span, text):
            return []
        return self._stand_in(span, text).left_out

    def stand_in(self, span: Span, text: str) -> str:
        """The face we ship that draws `text` when the span's own font can't: "Carlito Bold"."""
        return self._stand_in(span, text).face.name

    def _run(self, span: Span, text: str) -> tuple[pymupdf.Font, str]:
        """The face `text` is drawn in, and the text as it can be drawn.

        The span's own font if it draws every character; otherwise the whole run
        in the stand-in, less what even that can't draw.
        """
        embedded = self._embedded(span)
        if embedded is not None and not self.missing(span, text):
            return embedded.font, text
        stand_in = self._stand_in(span, text)
        return _face_font(stand_in.face), stand_in.text

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

        for pno, page_spans in by_page.items():
            self._pdf.erase_text(pno, [span.bbox for span in page_spans])

    def draw(
        self, span: Span, text: str, size: float | None = None, scale_x: float = 1.0
    ) -> list[str]:
        """Redraw `text` at the span's baseline, in its own font where the file has it.

        `size` replaces the span's own; `scale_x` narrows the run from its start.
        A character the font can't draw sends the whole run to the stand-in,
        so a line never mixes two faces. Returns, in plain words, anything that
        came out other than asked; empty when nothing did.
        """
        font_size = span.size if size is None else size

        # A font written by code gets codes, as the original did.
        coded = self._coded_for(span, text)
        if coded is not None:
            self._draw_codes(span, coded, text, font_size, scale_x)
            return []

        # Written by letter, in the file's own font when it has every letter.
        notices: list[str] = []
        embedded = self._embedded(span)
        if embedded is not None and not self.missing(span, text):
            try:
                alias = self._alias(span, embedded)
            except _FontUnusable as problem:  # the page wouldn't take the font
                notices.append(problem.reason)
            else:
                self._insert(span, text, alias, font_size, scale_x)
                return notices

        # Otherwise the stand-in draws the whole run, less what even it can't draw.
        stand_in = self._stand_in(span, text)
        if stand_in.left_out:
            notices.append(words.LEFT_OUT.format(letters=" ".join(stand_in.left_out)))
        alias = self._face_alias(span.page, stand_in.face)
        self._drawn.setdefault(stand_in.face, set()).update(stand_in.text)
        self._insert(span, stand_in.text, alias, font_size, scale_x)
        return notices

    def _insert(
        self, span: Span, text: str, fontname: str, size: float, scale_x: float
    ) -> None:
        """Write `text` at the span's baseline in `fontname`, in the span's color."""
        origin = pymupdf.Point(*span.origin)
        self.doc[span.page].insert_text(
            origin,
            text,
            fontname=fontname,
            fontsize=size,
            color=span.color,
            overlay=True,
            morph=(origin, pymupdf.Matrix(scale_x, 1)),
        )

    def _coded_for(self, span: Span, text: str) -> CodedFont | None:
        """The span's font drawn by code, if it has one and it can draw all of `text`."""
        embedded = self._embedded(span)
        if embedded is None or embedded.coded is None or self.missing(span, text):
            return None
        return embedded.coded

    def _draw_codes(
        self, span: Span, coded: CodedFont, text: str, size: float, scale_x: float
    ) -> None:
        """Write `text` as codes in the file's own font, on top of the page."""
        self._pdf.restore_font(span.page, coded.resource, coded.xref)
        x, y = self._pdf.to_pdf_space(span.page, span.origin)
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
        self._pdf.add_content(span.page, stream.encode())

    def _alias(self, span: Span, embedded: _EmbeddedFont) -> str:
        """The page's name for this span's font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises _FontUnusable when MuPDF won't add it.
        """
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._aliases:
            self._aliases[key] = self._add_font(span.page, font_name, embedded)
        found = self._aliases[key]
        if isinstance(found, _FontUnusable):
            raise _FontUnusable(found.reason)
        return found

    def _add_font(
        self, page: int, font_name: str, embedded: _EmbeddedFont
    ) -> str | _FontUnusable:
        """Add the file's copy of a font to a page under a new name, or say why it failed."""
        # Named from the font, so it can't clash with a name already on the page.
        digest = hashlib.blake2s(font_name.encode(), digest_size=_ALIAS_DIGEST_SIZE)
        alias = "F" + digest.hexdigest()
        try:
            self.doc[page].insert_font(fontname=alias, fontbuffer=embedded.font.buffer)
        except (RuntimeError, ValueError):
            # The bytes opened as a Font, but adding them to a page is another MuPDF
            # path that can still fail; the stand-in draws instead.
            return _FontUnusable(words.FONT_NOT_ADDED)
        return alias

    def _face_alias(self, page: int, face: Face) -> str:
        """The page's name for a face we ship, added to the page on first use.

        Once per page, as `_alias` does for the file's own fonts.
        """
        key = (page, face.file)
        if key not in self._face_aliases:
            # From the file's name, so it can't clash with the page's own names.
            digest = hashlib.blake2s(face.file.encode(), digest_size=_ALIAS_DIGEST_SIZE)
            alias = "S" + digest.hexdigest()
            xref = self.doc[page].insert_font(fontname=alias, fontbuffer=face_bytes(face))
            self._face_aliases[key] = alias
            self._faces_added[xref] = face
        return self._face_aliases[key]

    def save(self, path: str) -> list[str]:
        """Write the document to `path`, our faces cut to the letters drawn in them.

        Call it last: afterwards our faces can't draw any new letter.
        """
        notices: list[str] = []
        for xref, face in self._faces_added.items():
            try:
                font_file = trimmed(face, self._drawn[face])
            except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font)
                # The whole file still draws every letter; the file is only bigger.
                font_file = face_bytes(face)
                notices.append(words.FACE_NOT_TRIMMED.format(font=face.name))
            self._pdf.replace_font_file(xref, font_file)
        # Object streams compress the plain objects too: a face's width list is most of it.
        self.doc.save(path, garbage=_GARBAGE_COLLECT_MAX, deflate=True, use_objstms=True)
        return notices

    def absent(self, span: Span) -> bool:
        """Whether the span's text is gone from where it was. A black box would fail this.

        Only the span's own box on its own page is read: the same words
        elsewhere in the document are other text, not a leak. Spaces are
        ignored, so a leftover can't pass for gone by being spaced differently.
        """
        left = self._pdf.text_in(span.page, span.bbox)
        return _unspaced(span.text) not in _unspaced(left)

    def close(self) -> None:
        """Release the open document."""
        self.doc.close()

    def __enter__(self) -> MuPDFEngine:
        """Lets the engine be used as `with MuPDFEngine(path) as eng:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the document when the `with` block ends."""
        self.close()


def _open_font(pdf: PdfFile, page_font: PageFont) -> _EmbeddedFont:
    """Open the file's copy of a font. Raises _FontUnusable, saying why, if we can't use it.

    A font with no letter lookup of its own (only a symbol table, say) still
    counts if its letter list (ToUnicode) says which code draws each letter;
    `draw` then writes those codes.
    """
    font_file = pdf.font_bytes(page_font.xref)
    # Stored, but MuPDF can't read it out.
    if not font_file:
        raise _FontUnusable(words.FONT_UNREADABLE)
    font = pymupdf.Font(fontbuffer=font_file)
    claimed = font.valid_codepoints()
    coverage = Coverage(font_file, claimed)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return _EmbeddedFont(font, coverage, None)

    # Written by code, as its letter list says.
    try:
        coded, coded_coverage = _read_by_code(pdf, page_font, font_file)
    except _FontUnusable:
        # Unreadable either way: keep it only if MuPDF says it has letters.
        if claimed:
            return _EmbeddedFont(font, coverage, None)
        raise
    return _EmbeddedFont(font, coded_coverage, coded)


def _read_by_code(
    pdf: PdfFile, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage]:
    """The font's codes and which letters they really draw. Raises _FontUnusable if we can't."""
    # Only a TrueType font the page uses itself, not one inside a form (a reusable drawing).
    writable = page_font.file_type == "ttf" and not page_font.in_form
    if not writable:
        raise _FontUnusable(words.FONT_CANT_WRITE)
    coded = _read_coded_font(pdf, page_font)
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = Coverage(font_file, glyph_ids=glyph_ids)
    if not coverage.usable:
        raise _FontUnusable(words.FONT_UNREADABLE)
    return coded, coverage


def _read_coded_font(pdf: PdfFile, font: PageFont) -> CodedFont:
    """Which code draws each letter, from the font's letter list (ToUnicode).

    Raises _FontUnusable without one: guessing would draw the wrong letters.
    """
    code_bytes = _code_bytes(font)
    if code_bytes is None:
        raise _FontUnusable(words.FONT_CANT_WRITE)
    font_codes = pdf.font_codes(font.xref, code_bytes)
    if font_codes is None:
        raise _FontUnusable(words.FONT_NO_LETTER_LIST)

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        # Only codes that draw a shape, for a letter someone could type.
        typeable = code.glyph != 0 and not _is_control(code.letter)
        if typeable:
            letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        raise _FontUnusable(words.FONT_NO_LETTER_LIST)
    return CodedFont(font.resource, font.xref, code_bytes, letters)


def _code_bytes(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "Type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    return _CODE_BYTES.get(font.kind)


def _unspaced(text: str) -> str:
    """`text` with every space, tab and line break taken out."""
    return "".join(text.split())


def _is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")


@cache
def face_glyphs(face: Face) -> dict[str, float]:
    """Each letter a face we ship draws, within GLYPH_LIST_RANGES, to its width per 1000 em.

    The list the browser previews new text with, so it is kept short; letters
    past those ranges still draw, and the server's fit says so.
    """
    coverage = _face_coverage(face)
    letters = [
        chr(codepoint)
        for start, end in GLYPH_LIST_RANGES
        for codepoint in range(start, end)
        if coverage.covers(chr(codepoint))
    ]
    return _widths(_face_font(face), letters)


@cache
def _face_font(face: Face) -> pymupdf.Font:
    """A face we ship, opened once per process: it measures what `_face_alias` draws."""
    return pymupdf.Font(fontbuffer=face_bytes(face))


@cache
def _face_coverage(face: Face) -> Coverage:
    """Which letters a face we ship really draws, read from its file once per process."""
    return Coverage(face_bytes(face))


def _stand_in_run(face: Face, text: str) -> _StandIn:
    """`text` drawn in `face`: what it draws, and what it leaves out."""
    left_out = _face_coverage(face).missing(text)
    kept = "".join(ch for ch in text if ch not in left_out)
    return _StandIn(face, kept, left_out)


def _widths(font: pymupdf.Font, chars: list[str]) -> dict[str, float]:
    """Each character's width in `font`, per 1000 em."""
    return {ch: round(font.glyph_advance(ord(ch)) * _EM, _WIDTH_DP) for ch in chars}


def _round_box(box: Rect) -> Rect:
    """The box to 2 decimals, as span ids and the client see it."""
    dp = _POSITION_DP
    return Rect(round(box.x0, dp), round(box.y0, dp), round(box.x1, dp), round(box.y1, dp))


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    """The point to 2 decimals, like its box."""
    x, y = point
    return (round(x, _POSITION_DP), round(y, _POSITION_DP))


def _merge(pieces: list[TextPiece]) -> list[list[TextPiece]]:
    """Group a line's pieces into spans, one per run of same-style text.

    PDF writers split a sentence into many pieces to adjust letter spacing, so
    a piece is often a few letters and sometimes half a word. Without this,
    find-and-replace misses most real matches and the user can click something
    that is not a whole word.
    """
    if not pieces:
        return []
    groups = [[pieces[0]]]
    for piece in pieces[1:]:
        current_group = groups[-1]  # the newest group, the one still being built
        last_piece = current_group[-1]  # the piece just before this one on the line
        if _continues(last_piece, piece):
            current_group.append(piece)
        else:
            groups.append([piece])
    return groups


def _continues(previous: TextPiece, piece: TextPiece) -> bool:
    """Whether `piece` carries on the span that `previous` ends."""
    # Another font or size.
    if previous.font != piece.font:
        return False
    if abs(previous.size - piece.size) > SIZE_EPS:
        return False
    # Another baseline.
    _x, previous_y = previous.origin
    _x, y = piece.origin
    if abs(previous_y - y) > BASELINE_EPS:
        return False
    # Close enough: a small negative gap is kerning pulling letters together.
    gap = piece.box.x0 - previous.box.x1
    return -1.0 <= gap <= piece.size * GAP_RATIO
