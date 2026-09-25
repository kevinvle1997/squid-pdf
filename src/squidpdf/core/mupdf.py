"""PyMuPDF implementation of the engine.

Two things here are not obvious from the interface: fragments are merged into
logical spans at extraction, and the index is only ever built from the pristine
document.
"""

from __future__ import annotations

import hashlib

import pymupdf

from squidpdf.core.coverage import Coverage
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import base14_for, strip_subset, substitute_for
from squidpdf.core.types import Fragment, Rect, Span, SpanIndex, span_id

# Two runs belong to the same span when they sit on one baseline, share a face,
# and are close enough that the gap is kerning rather than a layout decision.
BASELINE_EPS = 0.6
SIZE_EPS = 0.1
GAP_RATIO = 0.35

_BYTE_MAX = 255  # one channel of PDF's packed 0xRRGGBB color, 0-255

_GARBAGE_COLLECT_MAX = 3  # PyMuPDF's highest level: dedupe + drop unused objects

# The index throws images away; decoding them was most of its time.
_INDEX_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

# insert_text draws base-14 fonts a byte per character; past Latin-1 comes out a dot.
_SIMPLE_FONT_CODES = 256

_EM = 1000  # advances are given per 1000 em, as PDF font widths are
_ADVANCE_DP = 2  # finer than any page can show

_ALIAS_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids


def _rgb(packed: int) -> tuple[float, float, float]:
    """PDF's packed 0xRRGGBB color into the (r, g, b) 0-1 floats PyMuPDF wants."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )


class MuPDFEngine:
    """Implements `core.engine.Engine`."""

    def __init__(self, path: str) -> None:
        """Open the PDF at `path` and set up the empty per-font caches."""
        self.path = path
        self.doc = pymupdf.open(path)
        # Keyed by (page, font name); each fills in lazily, on first lookup.
        self._fonts: dict[tuple[int, str], pymupdf.Font | None] = {}  # embedded font
        self._coverage: dict[tuple[int, str], Coverage] = {}  # its glyph coverage
        self._aliases: dict[tuple[int, str], str | None] = {}  # its page resource, once drawn

    def _embedded(self, page: int, name: str) -> pymupdf.Font | None:
        """The document's own font, or None when the file only references it.

        Not embedded is the common case for anything exported from Word, and it
        is exactly what the substitute state warns about.
        """
        key = (page, strip_subset(name))
        if key in self._fonts:
            return self._fonts[key]

        font = None
        for xref, ext, _t, basefont, *_ in self.doc[page].get_fonts(full=True):
            if strip_subset(basefont) != key[1]:
                continue
            if ext not in ("n/a", ""):
                try:
                    _basename, _ext, _type, buf = self.doc.extract_font(xref)
                    if buf:
                        font = pymupdf.Font(fontbuffer=buf)
                except (RuntimeError, ValueError):
                    pass
            break

        self._fonts[key] = font
        return font

    def _drawing_font(self, span: Span) -> pymupdf.Font:
        """The font to actually draw with: the real one, or its base-14 stand-in."""
        embedded = self._embedded(span.page, span.font)
        if embedded is not None:
            return embedded
        return pymupdf.Font(fontname=base14_for(span.font))

    def index(self) -> SpanIndex:
        """Built once, from the pristine document. Never rebuilt from a patched one.

        That invariant is what keeps span ids stable: re-extracting after an edit
        would change every id and dangle every reference the client holds.
        """
        spans: list[Span] = []
        ordinal = 0

        for pno in range(len(self.doc)):
            for block in self.doc[pno].get_text("dict", flags=_INDEX_FLAGS)["blocks"]:
                for line in block["lines"]:
                    for group in _merge(line["spans"]):
                        span = self._build(pno, group, ordinal)
                        if span is not None:
                            spans.append(span)
                            ordinal += 1

        return SpanIndex(spans)

    def _build(self, pno: int, group: list[dict], ordinal: int) -> Span | None:
        """Turn one merged group of raw fragments into a Span, or None if blank."""
        text = "".join(s["text"] for s in group)
        if not text.strip():
            return None

        frags = tuple(
            Fragment(
                text=s["text"],
                bbox=Rect(*(round(v, 2) for v in s["bbox"])),
                origin=tuple(round(v, 2) for v in s["origin"]),
            )
            for s in group
        )
        bbox = frags[0].bbox
        for f in frags[1:]:
            bbox = bbox.union(f.bbox)

        head = group[0]
        return Span(
            id=span_id(pno, bbox, head["font"], text, ordinal),
            page=pno,
            text=text,
            font=head["font"],
            size=round(head["size"], 2),
            color=_rgb(head["color"]),
            bbox=bbox,
            origin=frags[0].origin,
            fragments=frags,
        )

    def pages(self) -> list[tuple[float, float]]:
        """Each page's width and height in points, as displayed: rotation applied."""
        rects = [self.doc[pno].rect for pno in range(len(self.doc))]
        return [(r.width, r.height) for r in rects]

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page as a PNG, `scale` pixels per point, or only the `clip` box of it.

        No alpha channel: the page is white whatever the app's theme (Rule 2).
        """
        box = None if clip is None else pymupdf.Rect(clip.x0, clip.y0, clip.x1, clip.y1)
        pix = self.doc[page].get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), clip=box, alpha=False
        )
        return pix.tobytes("png")

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Judge every span in the index as exact or substitute."""
        out = []
        for span in index:
            if self._embedded(span.page, span.font) is not None:
                out.append(FidelityReport(span.id, Fidelity.EXACT, span.font))
            else:
                out.append(
                    FidelityReport(
                        span.id,
                        Fidelity.SUBSTITUTE,
                        span.font,
                        substitute_for(span.font),
                    )
                )
        return out

    def glyphs(self, span: Span) -> dict[str, float]:
        """Every character this span's drawing font really draws, to its advance per 1000 em.

        The font `measure` uses, so widths agree. A subset's emptied glyphs don't
        count; a font Coverage can't read falls back to MuPDF's list, a claim.
        """
        embedded = self._embedded(span.page, span.font)
        if embedded is None:
            font = pymupdf.Font(fontname=base14_for(span.font))
            chars = [chr(cp) for cp in font.valid_codepoints() if cp < _SIMPLE_FONT_CODES]
        else:
            font = embedded
            key = (span.page, strip_subset(span.font))
            cov = self._coverage.get(key)
            if cov is None:
                cov = self._coverage[key] = Coverage(embedded.buffer)
            drawable = cov.drawable()
            chars = (
                drawable
                if drawable is not None
                else [chr(cp) for cp in font.valid_codepoints()]
            )
        return {ch: round(font.glyph_advance(ord(ch)) * _EM, _ADVANCE_DP) for ch in chars}

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, in this span's font and size."""
        return self._drawing_font(span).text_length(text, fontsize=span.size)

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's font cannot actually draw.

        Asks the glyph to draw rather than trusting the charset: a subsetted
        font still lists glyphs whose outlines were emptied. See core.coverage.
        """
        embedded = self._embedded(span.page, span.font)
        if embedded is None:
            return []  # the substitute carries full Latin coverage

        key = (span.page, strip_subset(span.font))
        cov = self._coverage.get(key)
        if cov is None:
            cov = self._coverage[key] = Coverage(embedded.buffer)
        return cov.missing(text)

    def remove(self, spans: list[Span]) -> None:
        """Delete these spans' glyph runs. Real removal, not a covering rectangle.

        Each fragment is redacted separately: the union box of a multi-fragment
        span can overlap text belonging to something else. Each span's embedded
        font is resolved and cached before its own redaction runs: apply_redactions
        can drop a page's now-unused font resource, and if that font was only used
        by the text just removed, it would otherwise be gone by the time draw()
        goes looking for it.
        """
        by_page: dict[int, list[Span]] = {}
        for s in spans:
            by_page.setdefault(s.page, []).append(s)
            self._embedded(s.page, s.font)

        for pno, group in by_page.items():
            page = self.doc[pno]
            for span in group:
                for frag in span.fragments:
                    r = frag.bbox
                    page.add_redact_annot(pymupdf.Rect(r.x0, r.y0, r.x1, r.y1))
            page.apply_redactions(images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE)

    def draw(self, span: Span, text: str) -> None:
        """Redraw at the span's baseline, in its own font where the file has it.

        The font goes onto the page once, not once per span: a resource per span
        piled up on the page and made every redraw slower.
        """
        page = self.doc[span.page]
        key = (span.page, strip_subset(span.font))
        if key not in self._aliases:
            embedded = self._embedded(span.page, span.font)
            alias = None
            if embedded is not None:
                # Named from the font, so it can't clash with a resource already on the page.
                digest = hashlib.blake2s(key[1].encode(), digest_size=_ALIAS_DIGEST_SIZE)
                alias = "F" + digest.hexdigest()
                try:
                    page.insert_font(fontname=alias, fontbuffer=embedded.buffer)
                except (RuntimeError, ValueError):
                    # These bytes already parsed as a Font object in _embedded(), but
                    # embedding as a page resource is a different MuPDF code path;
                    # degrade to the substitute rather than fail the edit outright.
                    alias = None
            self._aliases[key] = alias
        alias = self._aliases[key]

        page.insert_text(
            pymupdf.Point(*span.origin),
            text,
            fontname=alias or base14_for(span.font),
            fontsize=span.size,
            color=span.color,
            overlay=True,
        )

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


def _merge(raw: list[dict]) -> list[list[dict]]:
    """Group a line's fragments into logical spans.

    Generators split a sentence into several show-text operators so they can
    insert kerning, so a fragment is often a few letters and sometimes half a
    word. Without this, find-and-replace misses most real matches and the user
    can click something that is not a whole word.
    """
    groups: list[list[dict]] = []
    for frag in raw:
        if groups and _continues(groups[-1][-1], frag):
            groups[-1].append(frag)
        else:
            groups.append([frag])
    return groups


def _continues(prev: dict, nxt: dict) -> bool:
    """Whether fragment `nxt` is a continuation of `prev`, on the same span."""
    if prev["font"] != nxt["font"]:
        return False
    if abs(prev["size"] - nxt["size"]) > SIZE_EPS:
        return False
    if abs(prev["origin"][1] - nxt["origin"][1]) > BASELINE_EPS:
        return False
    gap = nxt["bbox"][0] - prev["bbox"][2]
    # A small negative gap is kerning pulling letters together, not a new run.
    return -1.0 <= gap <= nxt["size"] * GAP_RATIO
