"""The one backend there is: PyMuPDF.

Only primitives here (see `core.backend`): the hard-to-read MuPDF calls come
from `core.pdf`, which the backend extends, and the everyday ones are below.
What to make of them is `core.engine`'s. `open_pdf` is the one way in:
nothing outside `core` learns that MuPDF is underneath.
"""

from __future__ import annotations

from functools import cache

import pymupdf

from squidpdf.core import faces
from squidpdf.core.constants import LIBRARY_VERSION
from squidpdf.core.engine import Engine, letter_widths
from squidpdf.core.errors import Damaged, Encrypted
from squidpdf.core.fonts import face_bytes
from squidpdf.core.pdf import MUPDF_ERRORS, PdfFile
from squidpdf.core.types import Face, Page, Rect

_GARBAGE_COLLECT_MAX = 3  # PyMuPDF's highest level: dedupe + drop unused objects

# What drew and judged a page; a new one means earlier images and fidelity may differ.
BUILD = f"mupdf-{pymupdf.mupdf_version}.fonts-{LIBRARY_VERSION}"


def open_pdf(path: str) -> Engine:
    """The PDF at `path`, open for editing. Use it in a `with`, or close it."""
    return Engine(MuPDFBackend(path))


@cache
def face_widths(face: Face) -> dict[str, float]:
    """Each letter a face we ship draws, within GLYPH_LIST_RANGES, to its width per 1000 em.

    For the font list, where there's no document to open: the same widths the
    engine gives a span drawn in the face.
    """
    return letter_widths(_face_font(face), faces.face_letters(face))


class _MuPDFFont:
    """A font file MuPDF has opened. Implements `core.backend.FontProgram`."""

    def __init__(self, font: pymupdf.Font) -> None:
        """Wrap a font MuPDF has opened."""
        self._font = font

    def claimed(self) -> list[int]:
        """Every code point MuPDF says the font maps; a trimmed font claims more."""
        return list(self._font.valid_codepoints())

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        return self._font.glyph_advance(ord(ch))

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points."""
        return self._font.text_length(text, fontsize=size)


@cache
def _face_font(face: Face) -> _MuPDFFont:
    """A face we ship, opened once per process: it measures what `add_font` draws."""
    return _MuPDFFont(pymupdf.Font(fontbuffer=face_bytes(face)))


class MuPDFBackend(PdfFile):
    """A PDF open in MuPDF. Implements `core.backend.Backend`."""

    def __init__(self, path: str) -> None:
        """Open the PDF at `path`, only ever as a PDF."""
        try:
            # Left to sniff, MuPDF opens a PNG as a document.
            doc = pymupdf.open(path, filetype="pdf")
        except pymupdf.FileDataError as exc:  # garbage, truncated or empty
            raise Damaged from exc
        if doc.needs_pass:  # it opens, but every page is locked behind a password
            doc.close()
            raise Encrypted
        super().__init__(doc)

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        pages = [self._doc[pno] for pno in range(len(self._doc))]
        return [Page(p.cropbox.width, p.cropbox.height, p.rotation) for p in pages]

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box.

        The clip is mapped into the rotated page, where MuPDF clips.
        """
        pg = self._doc[page]
        box = None if clip is None else pymupdf.Rect(clip.x0, clip.y0, clip.x1, clip.y1)
        pix = pg.get_pixmap(
            matrix=pg.derotation_matrix * pymupdf.Matrix(scale, scale),
            clip=None if box is None else box * pg.rotation_matrix,
            alpha=False,
        )
        return pix.tobytes("png")

    def open_font(self, font_file: bytes) -> _MuPDFFont:
        """Open a font file to measure with. Raises ValueError when MuPDF can't."""
        try:
            return _MuPDFFont(pymupdf.Font(fontbuffer=font_file))
        except MUPDF_ERRORS as exc:
            raise ValueError("MuPDF can't open this font file") from exc

    def face_font(self, face: Face) -> _MuPDFFont:
        """A face we ship, opened to measure with: it measures what `add_font` draws."""
        return _face_font(face)

    def add_font(self, page: int, name: str, font_file: bytes) -> int:
        """Add a font to the page under `name`; returns its object number.

        Raises ValueError when MuPDF won't add it.
        """
        try:
            return self._doc[page].insert_font(fontname=name, fontbuffer=font_file)
        except MUPDF_ERRORS as exc:
            raise ValueError(f"MuPDF won't add font {name} to page {page}") from exc

    def write_text(
        self,
        page: int,
        origin: tuple[float, float],
        text: str,
        font: str,
        size: float,
        color: tuple[float, float, float],
        scale_x: float,
    ) -> None:
        """Write `text` from `origin` on its baseline, in the font the page calls `font`.

        On top of everything on the page; `scale_x` narrows it from its start.
        """
        at = pymupdf.Point(*origin)
        self._doc[page].insert_text(
            at,
            text,
            fontname=font,
            fontsize=size,
            color=color,
            overlay=True,
            morph=(at, pymupdf.Matrix(scale_x, 1)),
        )

    def save(self, path: str) -> None:
        """Write the document to `path`, as small as MuPDF makes it."""
        # Object streams compress the plain objects too: a face's width list is most of it.
        self._doc.save(path, garbage=_GARBAGE_COLLECT_MAX, deflate=True, use_objstms=True)

    def close(self) -> None:
        """Release the open document."""
        self._doc.close()


def write_sample(path: str) -> None:
    """A two-page sample: one page whose fonts are only named, one where one is stored."""
    doc = pymupdf.open()
    # "tibo" and "tiro" are MuPDF's short names for Times Bold and Times Roman,
    # which it never puts in the file.
    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        "This agreement is made on 14 March 2026 between",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 146),
        "Wescott Analytics Ltd and Lindqvist & Rowe LLP.",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 176),
        "The Client shall pay 48,500 per quarter in arrears.",
        fontname="tiro",
        fontsize=11,
    )

    # Embedded then subsetted, the way a real generator leaves it, so only the
    # glyphs this page used survive and typing an accent will fail.
    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text((72, 96), "Schedule 1 - Scope of work", fontname="emb", fontsize=12)
    embedded.insert_text(
        (72, 124),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()
