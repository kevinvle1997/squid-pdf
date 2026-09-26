"""PyMuPDF implementation of the engine.

Two things here are not obvious from the interface: fragments are merged into
logical spans at extraction, and the index is only ever built from the pristine
document.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

import pymupdf

from squidpdf.core.constants import BASELINE_EPS, GAP_RATIO, LIBRARY_VERSION, SIZE_EPS
from squidpdf.core.coverage import Coverage
from squidpdf.core.engine import Unreadable
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import base14_for, strip_subset, substitute_for
from squidpdf.core.pdf import PageFont, PdfFile, TextPiece
from squidpdf.core.types import (
    CodedFont,
    FontCode,
    Fragment,
    Page,
    Rect,
    Span,
    SpanIndex,
    span_id,
)

_GARBAGE_COLLECT_MAX = 3  # PyMuPDF's highest level: dedupe + drop unused objects

# insert_text draws base-14 fonts a byte per character; past Latin-1 comes out a dot.
_SIMPLE_FONT_CODES = 256

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


class MuPDFEngine:
    """Implements `core.engine.Engine`."""

    def __init__(self, path: str) -> None:
        """Open the PDF at `path` and set up the empty per-font caches."""
        self.path = path
        try:
            # Only ever as a PDF: left to sniff, MuPDF opens a PNG as a document.
            self.doc = pymupdf.open(path, filetype="pdf")
        except pymupdf.FileDataError as exc:  # garbage, truncated or empty
            raise Unreadable(path) from exc
        self._pdf = PdfFile(self.doc)
        # Keyed by (page, font name); each fills in on first lookup.
        self._fonts: dict[tuple[int, str], _EmbeddedFont | None] = {}  # None: only named
        self._aliases: dict[tuple[int, str], str | None] = {}  # its page resource, once drawn

    def _embedded(self, span: Span) -> _EmbeddedFont | None:
        """The file's own copy of the span's font, or None when the file only names it.

        Only named is the common case for anything exported from Word, and it is
        exactly what the substitute state warns about.
        """
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._fonts:
            self._fonts[key] = self._load_font(span.page, font_name)
        return self._fonts[key]

    def _load_font(self, page: int, font_name: str) -> _EmbeddedFont | None:
        """Find the font on the page and open the file's copy of it."""
        # The first font by this name, in MuPDF's order; a later one is never tried.
        page_font = next(
            (font for font in self._pdf.fonts(page) if strip_subset(font.name) == font_name),
            None,
        )
        # Not on the page, or only named there.
        if page_font is None or not page_font.is_embedded:
            return None
        try:
            return _open_font(self._pdf, page_font)
        except (RuntimeError, ValueError):  # MuPDF can't open it
            return None

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
        return FidelityReport(
            span.id, Fidelity.SUBSTITUTE, span.font, substitute_for(span.font)
        )

    def glyphs(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em.

        The same font `measure` uses, so widths agree. A trimmed (subset) font's
        emptied letters don't count; if Coverage can't read the font, MuPDF's own
        list stands in.
        """
        embedded = self._embedded(span)

        # Not in the file: the stand-in font draws it.
        if embedded is None:
            stand_in = pymupdf.Font(fontname=base14_for(span.font))
            return _widths(stand_in, sorted(_simple_chars(stand_in)))

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
        substitute is checked against what it draws, as `glyphs` lists it.
        """
        embedded = self._embedded(span)
        # Not in the file: the substitute draws it.
        if embedded is None:
            drawable = _simple_chars(pymupdf.Font(fontname=base14_for(span.font)))
            return [ch for ch in dict.fromkeys(text) if ch not in drawable]
        return embedded.coverage.missing(text)

    def _run(self, span: Span, text: str) -> tuple[pymupdf.Font, str]:
        """The face `text` is drawn in, and the text as it can be drawn.

        The span's own font if it draws every character; otherwise the whole run
        in the substitute, less what even that can't draw.
        """
        embedded = self._embedded(span)
        if embedded is not None and not self.missing(span, text):
            return embedded.font, text
        substitute = pymupdf.Font(fontname=base14_for(span.font))
        drawable = _simple_chars(substitute)
        return substitute, "".join(ch for ch in text if ch in drawable)

    def remove(self, spans: list[Span]) -> None:
        """Delete these spans' text for real, not by covering it with a box.

        One box per span, not per fragment: the cost grows with the box count,
        and a span's box covers its fragments. Lines and underlines stay. Each
        span's font is read first: erasing can delete a font the page no longer
        uses, and `draw` still needs it.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
            self._embedded(span)

        for pno, page_spans in by_page.items():
            self._pdf.erase_text(pno, [span.bbox for span in page_spans])

    def draw(
        self, span: Span, text: str, size: float | None = None, scale_x: float = 1.0
    ) -> None:
        """Redraw `text` at the span's baseline, in its own font where the file has it.

        `size` replaces the span's own; `scale_x` narrows the run from its start.
        A character the font can't draw sends the whole run to the substitute,
        so a line never mixes two faces.
        """
        font_size = span.size if size is None else size

        # A font written by code gets codes, as the original did.
        coded = self._coded_for(span, text)
        if coded is not None:
            self._draw_codes(span, coded, text, font_size, scale_x)
            return

        # Written by letter: in the file's font if it draws them all, else the substitute.
        alias = None if self.missing(span, text) else self._alias(span)
        if alias is None:  # the substitute draws the whole run
            _font, text = self._run(span, text)
        origin = pymupdf.Point(*span.origin)
        self.doc[span.page].insert_text(
            origin,
            text,
            fontname=alias or base14_for(span.font),
            fontsize=font_size,
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

    def _alias(self, span: Span) -> str | None:
        """The page's name for this span's font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. None when the file only names the font, or MuPDF won't add it.
        """
        font_name = strip_subset(span.font)
        key = (span.page, font_name)
        if key not in self._aliases:
            self._aliases[key] = self._add_font(span, font_name)
        return self._aliases[key]

    def _add_font(self, span: Span, font_name: str) -> str | None:
        """Add the file's copy of the span's font to its page, under a new name."""
        embedded = self._embedded(span)
        # Only named in the file: nothing to add.
        if embedded is None:
            return None
        # Named from the font, so it can't clash with a name already on the page.
        digest = hashlib.blake2s(font_name.encode(), digest_size=_ALIAS_DIGEST_SIZE)
        alias = "F" + digest.hexdigest()
        try:
            self.doc[span.page].insert_font(fontname=alias, fontbuffer=embedded.font.buffer)
        except (RuntimeError, ValueError):
            # The bytes opened as a Font, but adding them to a page is another MuPDF
            # path that can still fail; the substitute draws instead.
            return None
        return alias

    def draw_at(
        self,
        page: int,
        origin: tuple[float, float],
        text: str,
        size: float,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        """Draw where the document has no text: a signature, an annotation."""
        self.doc[page].insert_text(
            pymupdf.Point(*origin), text, fontsize=size, color=color, overlay=True
        )

    def save(self, path: str) -> None:
        """Write the (possibly edited) document to `path`."""
        self.doc.save(path, garbage=_GARBAGE_COLLECT_MAX, deflate=True)

    def absent(self, text: str) -> bool:
        """Confirm removed text is really gone. A black rectangle would fail this."""
        return not any(text in self.doc[i].get_text() for i in range(len(self.doc)))

    def close(self) -> None:
        """Release the open document."""
        self.doc.close()

    def __enter__(self) -> MuPDFEngine:
        """Lets the engine be used as `with MuPDFEngine(path) as eng:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the document when the `with` block ends."""
        self.close()


def _open_font(pdf: PdfFile, page_font: PageFont) -> _EmbeddedFont | None:
    """Open the file's copy of a font, or None if it draws nothing we can use.

    A font with no letter lookup of its own (only a symbol table, say) still
    counts if its letter list (ToUnicode) says which code draws each letter;
    `draw` then writes those codes.
    """
    font_file = pdf.font_bytes(page_font.xref)
    # Stored, but MuPDF can't read it out.
    if not font_file:
        return None
    font = pymupdf.Font(fontbuffer=font_file)
    claimed = font.valid_codepoints()
    coverage = Coverage(font_file, claimed)

    # Looks letters up itself: the usual case.
    if coverage.usable:
        return _EmbeddedFont(font, coverage, None)

    # Written by code, as its letter list says.
    by_code = _read_by_code(pdf, page_font, font_file)
    if by_code is not None:
        coded, coded_coverage = by_code
        return _EmbeddedFont(font, coded_coverage, coded)

    # Unreadable either way: keep it only if MuPDF says it has letters.
    if claimed:
        return _EmbeddedFont(font, coverage, None)
    return None


def _read_by_code(
    pdf: PdfFile, page_font: PageFont, font_file: bytes
) -> tuple[CodedFont, Coverage] | None:
    """The font's codes and which letters they really draw, if we can write it by code."""
    # Only a TrueType font the page uses itself, not one inside a form (a reusable drawing).
    writable = page_font.file_type == "ttf" and not page_font.in_form
    if not writable:
        return None
    coded = _read_coded_font(pdf, page_font)
    if coded is None:
        return None
    glyph_ids = {letter: code.glyph for letter, code in coded.letters.items()}
    coverage = Coverage(font_file, glyph_ids=glyph_ids)
    if not coverage.usable:
        return None
    return coded, coverage


def _read_coded_font(pdf: PdfFile, font: PageFont) -> CodedFont | None:
    """Which code draws each letter, from the font's letter list (ToUnicode).

    None without a letter list: guessing would draw the wrong letters.
    """
    code_bytes = _code_bytes(font)
    if code_bytes is None:
        return None
    font_codes = pdf.font_codes(font.xref, code_bytes)
    if font_codes is None:
        return None

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        # Only codes that draw a shape, for a letter someone could type.
        typeable = code.glyph != 0 and not _is_control(code.letter)
        if typeable:
            letters.setdefault(code.letter, code)  # a letter with two codes keeps the lowest
    if not letters:
        return None
    return CodedFont(font.resource, font.xref, code_bytes, letters)


def _code_bytes(font: PageFont) -> int | None:
    """How many bytes each code takes in this font, or None if we can't write it."""
    if font.kind == "Type0" and font.encoding != "Identity-H":
        return None  # its codes aren't glyph numbers, so we can't work them out
    return _CODE_BYTES.get(font.kind)


def _is_control(ch: str) -> bool:
    """A control, format, private-use or unassigned character: nothing to type."""
    return unicodedata.category(ch).startswith("C")


def _simple_chars(font: pymupdf.Font) -> set[str]:
    """What a standard (base-14) font draws through insert_text: its letters within Latin-1."""
    return {chr(cp) for cp in font.valid_codepoints() if cp < _SIMPLE_FONT_CODES}


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
