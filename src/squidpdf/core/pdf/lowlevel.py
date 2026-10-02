"""The only file that uses MuPDF's low-level API.

Everyday PyMuPDF calls (insert_text, get_pixmap, save) are easy to read, so
they stay in `core.pdf.mupdf`, whose driver holds a `PdfFile` for the rest. The
hard-to-read calls live here, and their results come back as named
dataclasses. This file only reports what the PDF says; the engine decides
what to do with it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.app.message import Message
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.types import FontCode, GlyphId, Rect

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255
_OPACITY_PREFIX = "SquidOpacity"  # our graphics states' names, e.g. SquidOpacity600 for 0.6
_PERMILLE = 1000  # opacity is written to a thousandth, far finer than the eye sees

_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# What PyMuPDF raises when MuPDF can't do what it was asked: MuPDF's own errors,
# which aren't RuntimeErrors, and the RuntimeErrors and ValueErrors PyMuPDF adds.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)
# MuPDF's own errors alone, which hold a pointer, so they can't cross between processes.
MUPDF_OWN_ERRORS = pymupdf.mupdf.FzErrorBase
# The one of them that means the work was too big, not the file broken: past MuPDF's limit.
MUPDF_TOO_HEAVY = pymupdf.mupdf.FzErrorLimit
# The one that means the machine failed, not the file: out of memory, or a file it can't open.
MUPDF_SYSTEM_ERRORS = pymupdf.mupdf.FzErrorSystem


@dataclass(frozen=True, slots=True, eq=False)
class PdfFile:
    """An open PDF, as MuPDF's low-level API sees it: a few plain methods over it."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first.

        None if the font has no letter list (its ToUnicode) or MuPDF can't read
        that list; DriverError if MuPDF can't load the font at all. `code_bytes`
        is 1 for a simple font, 2 for a Type0 (two-byte) font.
        """
        value_type, _value = self.doc.xref_get_key(xref, "ToUnicode")
        if value_type == _PDF_NULL:
            return None

        with self._mupdf_font_record(xref) as font:
            if font.to_unicode is None:  # MuPDF couldn't read it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Type0: one code per glyph, so stop after the last glyph
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (_font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` is read under, often the font file's own.

        A page can list a font as "Carlito Bold" while its text reads "Carlito-Bold",
        the name inside the file. None when MuPDF can't load the font.
        """
        try:
            with self._mupdf_font_record(xref) as font:
                return pymupdf.mupdf.ll_fz_font_name(font.font)
        except DriverError:  # MuPDF can't load the font
            return None

    @contextmanager
    def _mupdf_font_record(self, xref: int) -> Iterator[pymupdf.mupdf.pdf_font_desc]:
        """MuPDF's own record of font `xref`: its letter list, codes and glyph widths.

        MuPDF's C function pdf_load_font builds the record and pdf_drop_font
        frees it; Python never frees it, so it is only lent out inside a `with`.
        DriverError if MuPDF can't load the font.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, None, mu.pdf_load_object(pdf, xref).m_internal
            )
        except MUPDF_ERRORS as exc:  # MuPDF can't make sense of the font object
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        try:
            yield font
        finally:
            mu.ll_pdf_drop_font(font)

    def redact(self, page: int, boxes: list[Rect]) -> None:
        """Delete every letter whose box touches one of `boxes`, and nothing else."""
        pg = self.doc[page]
        for box in boxes:
            pg.add_redact_annot(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1))
        pg.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
        )

    def restore_font(self, page: int, resource: str, xref: int) -> None:
        """Point the page's resource name `resource` at font `xref`, already in the file.

        Erasing text can remove a font the page no longer uses: this puts it
        back. Nothing new is added to the file.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        # An empty m_internal means the key isn't there yet, so make it.
        if not resources.m_internal:
            resources = mu.pdf_dict_put_dict(page_obj, mu.PDF_ENUM_NAME_Resources, 1)
        fonts = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Font)
        if not fonts.m_internal:
            fonts = mu.pdf_dict_put_dict(resources, mu.PDF_ENUM_NAME_Font, 1)
        mu.pdf_dict_puts(fonts, resource, mu.pdf_new_indirect(pdf, xref, 0))

    def add_opacity(self, page: int, opacity: float) -> str:
        """The page's name for a graphics state that fills at `opacity`, written to the page.

        Rounded to a thousandth and named for it, so writing it again changes nothing.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        permille = round(opacity * _PERMILLE)
        name = f"{_OPACITY_PREFIX}{permille}"
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        # An empty m_internal means the key isn't there yet, so make it.
        if not resources.m_internal:
            resources = mu.pdf_dict_put_dict(page_obj, mu.PDF_ENUM_NAME_Resources, 1)
        states = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_ExtGState)
        if not states.m_internal:
            states = mu.pdf_dict_put_dict(resources, mu.PDF_ENUM_NAME_ExtGState, 1)
        state = mu.pdf_new_dict(pdf, 1)
        mu.pdf_dict_put_real(state, mu.PDF_ENUM_NAME_ca, permille / _PERMILLE)
        mu.pdf_dict_puts(states, name, state)
        return name

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page as you see it into the PDF's own coordinates.

        Not page.transformation_matrix: on a turned page it forgets where the
        page's box starts.
        """
        mu = pymupdf.mupdf
        pg = self.doc[page]
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(mu.pdf_page_from_fz_page(pg.this), _mediabox, page_to_screen)
        moved = pymupdf.Point(*point) * pg.rotation_matrix * ~pymupdf.Matrix(page_to_screen)
        return (moved.x, moved.y)

    def _pdf(self) -> pymupdf.mupdf.PdfDocument:
        """The same document, as MuPDF's low-level API needs it."""
        return pymupdf.mupdf.pdf_document_from_fz_document(self.doc.this)


def _font_code(font: pymupdf.mupdf.pdf_font_desc, value: int) -> FontCode | None:
    """What code `value` draws in a font MuPDF has loaded, or None if it isn't one letter."""
    mu = pymupdf.mupdf
    codepoint = mu.ll_pdf_lookup_cmap(font.to_unicode, value)
    # No letter, or several (like "fi").
    if not 0 <= codepoint <= sys.maxunicode:
        return None
    # The font's internal number for the code, which picks its shape and width.
    cid = mu.ll_pdf_lookup_cmap(font.encoding, value)
    return FontCode(
        value=value,
        letter=chr(codepoint),
        glyph=GlyphId(mu.ll_pdf_font_cid_to_gid(font, cid)),
        width=mu.ll_pdf_lookup_hmtx(font, cid).w,
    )
