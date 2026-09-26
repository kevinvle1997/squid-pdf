"""The one place that talks to MuPDF's low-level API.

PyMuPDF's everyday calls (insert_text, get_pixmap, save) read fine and stay
in the engine. What lives here is the rest: raw C bindings, tuples and dicts
with no names. Everything leaves as a typed dataclass, and nothing here
decides anything: it reports what the file says, and the engine chooses.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pymupdf

from squidpdf.core.types import Rect

_BYTE_MAX = 255  # one channel of PDF's packed 0xRRGGBB color, 0-255

# Text extraction throws images away; decoding them was most of its time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

_ONE_BYTE_CODES = 256  # a simple font's codes are one byte


@dataclass(frozen=True, slots=True)
class TextPiece:
    """One run of text as the page draws it, often only part of a word."""

    text: str
    font: str
    size: float
    color: tuple[float, float, float]  # r, g, b, each 0-1
    box: Rect
    origin: tuple[float, float]  # where its baseline starts


@dataclass(frozen=True, slots=True)
class PageFont:
    """A font a page uses."""

    xref: int  # its PDF object
    name: str  # e.g. "ABCDEF+Arial"
    kind: str  # "TrueType", "Type0", ...
    file_type: str  # "ttf", "cff", ...; "" or "n/a" when not embedded
    resource: str  # its name in the page's font resources, e.g. "F1"
    encoding: str  # "Identity-H", "WinAnsiEncoding", ...
    in_form: bool  # used inside a form, not by the page itself

    @property
    def is_embedded(self) -> bool:
        """Whether the file carries the font itself, not just its name."""
        return self.file_type not in ("n/a", "")


@dataclass(frozen=True, slots=True)
class FontCode:
    """One code a font's ToUnicode names, and what it draws."""

    value: int  # the code the page writes, e.g. 0x21
    letter: str  # what ToUnicode says it is
    glyph: int  # the glyph it draws; 0 means none
    width: float  # per 1000 em, from /Widths or /W


class PdfFile:
    """Plain questions and edits on an open document, answered through MuPDF."""

    def __init__(self, doc: pymupdf.Document) -> None:
        """Wrap a document the caller opened, and still owns and closes."""
        self._doc = doc

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, as the pieces it is drawn in."""
        lines = []
        for block in self._doc[page].get_text("dict", flags=_TEXT_FLAGS)["blocks"]:
            for line in block["lines"]:
                lines.append([_text_piece(raw) for raw in line["spans"]])
        return lines

    def fonts(self, page: int) -> list[PageFont]:
        """Every font the page uses, in its forms too."""
        return [
            PageFont(
                xref=xref,
                name=name,
                kind=kind,
                file_type=file_type,
                resource=resource,
                encoding=encoding,
                in_form=referencer != 0,
            )
            for xref, file_type, kind, name, resource, encoding, referencer in self._doc[
                page
            ].get_fonts(full=True)
        ]

    def font_bytes(self, xref: int) -> bytes | None:
        """The font program the file embeds, or None when MuPDF can't get it out."""
        try:
            _name, _ext, _kind, buffer = self._doc.extract_font(xref)
        except (RuntimeError, ValueError):
            return None
        return buffer or None

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Every code the font's ToUnicode names, lowest first.

        None without a ToUnicode MuPDF can read. A code for several letters
        (a ligature) is left out. `code_bytes` is 1 for a simple font, 2 for
        Identity-H. Read through MuPDF's own font loader, so each glyph is
        the one it renders.
        """
        if self._doc.xref_get_key(xref, "ToUnicode")[0] == "null":
            return None

        mu = pymupdf.mupdf
        pdf = self._pdf()
        font = mu.ll_pdf_load_font(
            pdf.m_internal, None, mu.pdf_load_object(pdf, xref).m_internal
        )
        try:
            if font.to_unicode is None:  # MuPDF couldn't parse it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Identity-H: codes are CIDs, and none past the last glyph draws
                code_count = font.cid_to_gid_len or font.font.glyph_count
            found = []
            for value in range(code_count):
                letter = mu.ll_pdf_lookup_cmap(font.to_unicode, value)
                if not 0 <= letter <= sys.maxunicode:  # unmapped, or a ligature
                    continue
                cid = mu.ll_pdf_lookup_cmap(font.encoding, value)
                found.append(
                    FontCode(
                        value=value,
                        letter=chr(letter),
                        glyph=mu.ll_pdf_font_cid_to_gid(font, cid),
                        width=mu.ll_pdf_lookup_hmtx(font, cid).w,
                    )
                )
            return found
        finally:
            mu.ll_pdf_drop_font(font)

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Really delete the text inside these boxes; images and line art stay."""
        pg = self._doc[page]
        for box in boxes:
            pg.add_redact_annot(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1))
        pg.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
        )

    def restore_font(self, page: int, resource: str, xref: int) -> None:
        """Make `resource` name font `xref` on the page again; erasing drops unused ones.

        Written where the page finds its resources, inherited or not, so no
        font is added.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        if not resources.m_internal:
            resources = mu.pdf_dict_put_dict(page_obj, mu.PDF_ENUM_NAME_Resources, 1)
        fonts = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Font)
        if not fonts.m_internal:
            fonts = mu.pdf_dict_put_dict(resources, mu.PDF_ENUM_NAME_Font, 1)
        mu.pdf_dict_puts(fonts, resource, mu.pdf_new_indirect(pdf, xref, 0))

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """A point in the unrotated page's space, as the PDF's own content sees it.

        Not via page.transformation_matrix: it drops the mediabox offset on a
        rotated page.
        """
        mu = pymupdf.mupdf
        pg = self._doc[page]
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(mu.pdf_page_from_fz_page(pg.this), _mediabox, page_to_screen)
        moved = pymupdf.Point(*point) * pg.rotation_matrix * ~pymupdf.Matrix(page_to_screen)
        return (moved.x, moved.y)

    def add_content(self, page: int, stream: bytes) -> None:
        """Draw `stream` on top of everything on the page.

        The page's own drawing is wrapped in q/Q first, so no state it leaves
        set leaks into ours.
        """
        pg = self._doc[page]
        if not pg.is_wrapped:
            pg.wrap_contents()
        xref = self._doc.get_new_xref()
        self._doc.update_object(xref, "<<>>")
        self._doc.update_stream(xref, stream)
        parts = [*pg.get_contents(), xref]
        self._doc.xref_set_key(
            pg.xref, "Contents", "[" + " ".join(f"{p} 0 R" for p in parts) + "]"
        )

    def _pdf(self) -> pymupdf.mupdf.PdfDocument:
        """The document as MuPDF's low-level API sees it."""
        return pymupdf.mupdf.pdf_document_from_fz_document(self._doc.this)


def _text_piece(raw: dict) -> TextPiece:
    """One span dict from get_text("dict"), named."""
    return TextPiece(
        text=raw["text"],
        font=raw["font"],
        size=raw["size"],
        color=_rgb(raw["color"]),
        box=Rect(*raw["bbox"]),
        origin=(raw["origin"][0], raw["origin"][1]),
    )


def _rgb(packed: int) -> tuple[float, float, float]:
    """PDF's packed 0xRRGGBB color as r, g, b, each 0-1."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
