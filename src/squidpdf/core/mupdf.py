"""PyMuPDF implementation of the engine.

Two things here are not obvious from the interface: fragments are merged into
logical spans at extraction, and the index is only ever built from the pristine
document.
"""

from __future__ import annotations

import hashlib
import unicodedata

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

_EM = 1000  # advances are given per 1000 em, as PDF font widths are
_ADVANCE_DP = 2  # finer than any page can show

# Bytes per code, for the font kinds we can write by code.
_CODE_BYTES = {"TrueType": 1, "Type0": 2}

_ALIAS_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel

# What drew and judged a page; a new one means earlier images and fidelity may differ.
BUILD = f"mupdf-{pymupdf.mupdf_version}.fonts-{LIBRARY_VERSION}"


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
        # Keyed by (page, font name); each fills in lazily, on first lookup.
        self._fonts: dict[tuple[int, str], pymupdf.Font | None] = {}  # embedded font
        self._coverage: dict[tuple[int, str], Coverage] = {}  # its glyph coverage, if kept
        self._aliases: dict[tuple[int, str], str | None] = {}  # its page resource, once drawn
        self._coded: dict[tuple[int, str], CodedFont] = {}  # fonts drawn by code, not by letter

    def _embedded(self, page: int, name: str) -> pymupdf.Font | None:
        """The document's own font, or None when the file only references it.

        Not embedded is the common case for anything exported from Word, and it
        is exactly what the substitute state warns about. A font with no letter
        lookup (only a symbol cmap, say) counts only if its ToUnicode tells us
        which code draws each letter; `draw` then writes those codes.
        """
        key = (page, strip_subset(name))
        if key in self._fonts:
            return self._fonts[key]

        font = None
        for page_font in self._pdf.fonts(page):
            if strip_subset(page_font.name) != key[1]:
                continue
            if page_font.is_embedded:
                try:
                    buf = self._pdf.font_bytes(page_font.xref)
                    if buf:
                        candidate = pymupdf.Font(fontbuffer=buf)
                        claimed = candidate.valid_codepoints()
                        cov = Coverage(buf, claimed)
                        # Only fonts on the page itself, not inside a form.
                        if (
                            not cov.usable
                            and page_font.file_type == "ttf"
                            and not page_font.in_form
                        ):
                            coded = _read_coded_font(self._pdf, page_font)
                            if coded is not None:
                                glyph_ids = {ch: c.glyph for ch, c in coded.letters.items()}
                                coded_cov = Coverage(buf, glyph_ids=glyph_ids)
                                if coded_cov.usable:
                                    cov, self._coded[key] = coded_cov, coded
                        if cov.usable or claimed:
                            font, self._coverage[key] = candidate, cov
                except (RuntimeError, ValueError):  # MuPDF can't open it
                    pass
            break

        self._fonts[key] = font
        return font

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

        frags = tuple(
            Fragment(
                text=piece.text,
                bbox=_round_box(piece.box),
                origin=(round(piece.origin[0], 2), round(piece.origin[1], 2)),
            )
            for piece in group
        )
        bbox = frags[0].bbox
        for f in frags[1:]:
            bbox = bbox.union(f.bbox)

        head = group[0]
        return Span(
            id=span_id(pno, bbox, head.font, text, ordinal),
            page=pno,
            text=text,
            font=head.font,
            size=round(head.size, 2),
            color=head.color,
            bbox=bbox,
            origin=frags[0].origin,
            fragments=frags,
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
        in_file = self._embedded(span.page, span.font) is not None
        exact = in_file and not self.missing(span, span.text)
        if exact:
            return FidelityReport(span.id, Fidelity.EXACT, span.font)
        return FidelityReport(
            span.id, Fidelity.SUBSTITUTE, span.font, substitute_for(span.font)
        )

    def glyphs(self, span: Span) -> dict[str, float]:
        """Every character this span's drawing font really draws, to its advance per 1000 em.

        The font `measure` uses, so widths agree, and what `missing` checks
        against. A subset's emptied glyphs don't count; a font Coverage can't
        read falls back to MuPDF's list, a claim.
        """
        key = (span.page, strip_subset(span.font))
        embedded = self._embedded(span.page, span.font)

        # Not in the file: the stand-in font draws it.
        if embedded is None:
            stand_in = pymupdf.Font(fontname=base14_for(span.font))
            return _advances(stand_in, sorted(_simple_chars(stand_in)))

        chars = self._coverage[key].drawable()

        # Written by code: widths come from the font's width list.
        coded = self._coded.get(key)
        if coded is not None:
            return {ch: round(coded.letters[ch].width, _ADVANCE_DP) for ch in chars}

        # Written by letter: widths come from the font itself.
        return _advances(embedded, chars)

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, in the face `draw` would pick and this span's size."""
        coded = self._coded_for(span, text)
        if coded is not None:
            return sum(coded.letters[ch].width for ch in text) * span.size / _EM
        font, text = self._run(span, text)
        return font.text_length(text, fontsize=span.size)

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's font cannot actually draw.

        Asks the glyph to draw rather than trusting the charset: a subsetted
        font still lists glyphs whose outlines were emptied. See core.coverage.
        A substitute is checked against what it draws, as `glyphs` lists it.
        """
        embedded = self._embedded(span.page, span.font)
        if embedded is None:
            drawable = _simple_chars(pymupdf.Font(fontname=base14_for(span.font)))
            return [ch for ch in dict.fromkeys(text) if ch not in drawable]
        return self._coverage[(span.page, strip_subset(span.font))].missing(text)

    def _run(self, span: Span, text: str) -> tuple[pymupdf.Font, str]:
        """The face `text` is drawn in, and the text as it can be drawn.

        The span's own font if it draws every character; otherwise the whole run
        in the substitute, less what even that can't draw.
        """
        embedded = self._embedded(span.page, span.font)
        if embedded is not None and not self.missing(span, text):
            return embedded, text
        substitute = pymupdf.Font(fontname=base14_for(span.font))
        drawable = _simple_chars(substitute)
        return substitute, "".join(ch for ch in text if ch in drawable)

    def remove(self, spans: list[Span]) -> None:
        """Delete these spans' glyph runs. Real removal, not a covering rectangle.

        One box per span, not per fragment: MuPDF's cost grows with the box
        count, and a span's fragments only ever sit a kerning gap apart, so its
        box covers nothing theirs don't. Line art is left alone, so underlines
        and rules survive. Each span's embedded font is resolved and cached
        before its own redaction runs, with its resource name and codes:
        apply_redactions can drop a page's now-unused font resource, and if
        that font was only used by the text just removed, it would otherwise
        be gone by the time draw() goes looking for it.
        """
        by_page: dict[int, list[Span]] = {}
        for s in spans:
            by_page.setdefault(s.page, []).append(s)
            self._embedded(s.page, s.font)

        for pno, group in by_page.items():
            self._pdf.erase_text(pno, [span.bbox for span in group])

    def draw(
        self, span: Span, text: str, size: float | None = None, scale_x: float = 1.0
    ) -> None:
        """Redraw at the span's baseline, in its own font where the file has it.

        `size` replaces the span's own; `scale_x` narrows the run from its start.
        A character the font can't draw sends the whole run to the substitute,
        so the line never mixes two faces. The font goes onto the page once,
        not once per span: a resource per span piled up on the page and made
        every redraw slower. A font drawn by code gets codes, as the original did.
        """
        coded = self._coded_for(span, text)
        if coded is not None:
            self._draw_codes(span, coded, text, span.size if size is None else size, scale_x)
            return
        alias = None if self.missing(span, text) else self._alias(span)
        if alias is None:  # the substitute draws the whole run
            _font, text = self._run(span, text)
        origin = pymupdf.Point(*span.origin)
        self.doc[span.page].insert_text(
            origin,
            text,
            fontname=alias or base14_for(span.font),
            fontsize=span.size if size is None else size,
            color=span.color,
            overlay=True,
            morph=(origin, pymupdf.Matrix(scale_x, 1)),
        )

    def _coded_for(self, span: Span, text: str) -> CodedFont | None:
        """The span's font drawn by code, if it has one and it can draw all of `text`."""
        coded = self._coded.get((span.page, strip_subset(span.font)))
        if coded is None or self.missing(span, text):
            return None
        return coded

    def _draw_codes(
        self, span: Span, coded: CodedFont, text: str, size: float, scale_x: float
    ) -> None:
        """Write `text` as codes in the file's own font, on top of the page."""
        self._pdf.restore_font(span.page, coded.resource, coded.xref)
        x, y = self._pdf.to_pdf_space(span.page, span.origin)
        r, g, b = span.color
        hex_codes = "".join(
            f"{coded.letters[ch].value:0{coded.code_bytes * 2}x}" for ch in text
        )
        stream = (
            f"q BT {r:.{_PDF_DP}f} {g:.{_PDF_DP}f} {b:.{_PDF_DP}f} rg"
            f" /{coded.resource} {size:.{_PDF_DP}f} Tf"
            f" {scale_x:.{_PDF_DP}f} 0 0 1 {x:.{_PDF_DP}f} {y:.{_PDF_DP}f} Tm"
            f" <{hex_codes}> Tj ET Q"
        )
        self._pdf.add_content(span.page, stream.encode())

    def _alias(self, span: Span) -> str | None:
        """The page's resource name for this span's embedded font, put there on first use.

        None when the file only references the font, or MuPDF won't embed it.
        """
        key = (span.page, strip_subset(span.font))
        if key not in self._aliases:
            embedded = self._embedded(span.page, span.font)
            alias = None
            if embedded is not None:
                # Named from the font, so it can't clash with a resource already on the page.
                digest = hashlib.blake2s(key[1].encode(), digest_size=_ALIAS_DIGEST_SIZE)
                alias = "F" + digest.hexdigest()
                try:
                    self.doc[span.page].insert_font(fontname=alias, fontbuffer=embedded.buffer)
                except (RuntimeError, ValueError):
                    # These bytes already parsed as a Font object in _embedded(), but
                    # embedding as a page resource is a different MuPDF code path;
                    # degrade to the substitute rather than fail the edit outright.
                    alias = None
            self._aliases[key] = alias
        return self._aliases[key]

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


def _read_coded_font(pdf: PdfFile, font: PageFont) -> CodedFont | None:
    """Which code, glyph and width draws each letter, from the font's ToUnicode.

    None without one: guessing would draw the wrong letters. Skips control and
    private-use characters, and codes with no glyph. A letter with two codes
    keeps the lowest.
    """
    code_bytes = _code_bytes(font)
    if code_bytes is None:
        return None
    font_codes = pdf.font_codes(font.xref, code_bytes)
    if font_codes is None:
        return None

    letters: dict[str, FontCode] = {}
    for code in font_codes:  # lowest code first
        if code.glyph and not _is_control(code.letter):
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
    """What a base-14 font draws through insert_text: its glyphs within Latin-1."""
    return {chr(cp) for cp in font.valid_codepoints() if cp < _SIMPLE_FONT_CODES}


def _advances(font: pymupdf.Font, chars: list[str]) -> dict[str, float]:
    """Each character's advance in `font`, per 1000 em."""
    return {ch: round(font.glyph_advance(ord(ch)) * _EM, _ADVANCE_DP) for ch in chars}


def _round_box(box: Rect) -> Rect:
    """The box to 2 decimals, as span ids and the client see it."""
    return Rect(round(box.x0, 2), round(box.y0, 2), round(box.x1, 2), round(box.y1, 2))


def _merge(pieces: list[TextPiece]) -> list[list[TextPiece]]:
    """Group a line's fragments into logical spans.

    Generators split a sentence into several show-text operators so they can
    insert kerning, so a fragment is often a few letters and sometimes half a
    word. Without this, find-and-replace misses most real matches and the user
    can click something that is not a whole word.
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


def _continues(prev: TextPiece, nxt: TextPiece) -> bool:
    """Whether piece `nxt` carries on from `prev`, in the same span."""
    if prev.font != nxt.font:
        return False
    if abs(prev.size - nxt.size) > SIZE_EPS:
        return False
    if abs(prev.origin[1] - nxt.origin[1]) > BASELINE_EPS:
        return False
    gap = nxt.box.x0 - prev.box.x1
    # A small negative gap is kerning pulling letters together, not a new run.
    return -1.0 <= gap <= nxt.size * GAP_RATIO
