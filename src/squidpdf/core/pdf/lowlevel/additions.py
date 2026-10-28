"""What the driver adds to a page: a font put back, an opacity, a drawing of its own."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import pdf_of
from squidpdf.core.pdf.lowlevel.pages import to_pdf_matrix
from squidpdf.core.types import SOLID

_OPACITY_PREFIX = "SquidOpacity"  # our graphics states' names, e.g. SquidOpacity600 for 0.6

_PERMILLE = 1000  # opacity is written to a thousandth, far finer than the eye sees

# PyMuPDF's name for a graphics state: strokes at 99 hundredths (solid), fills at {:02d}.
_FILL_OPACITY_NAME = "fitzca99{:02d}"

_PERCENT = 100  # a fill opacity is named to a hundredth, 0 to 99


@dataclass(frozen=True, slots=True, eq=False)
class PageAdditions:
    """What the driver adds to a page: a font, an opacity, a drawing, and where it goes."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def restore_font(self, page: int, resource: str, font_object: int) -> None:
        """Point the page's resource name `resource` at font `font_object`, already in the file.

        Erasing text can remove a font the page no longer uses: this puts it
        back. Nothing new is added to the file.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        resources = _page_resources(mu.pdf_lookup_page_obj(pdf, page))
        fonts = _dictionary_under(resources, mu.PDF_ENUM_NAME_Font)
        mu.pdf_dict_puts(fonts, resource, mu.pdf_new_indirect(pdf, font_object, 0))

    def add_opacity(self, page: int, opacity: float) -> str:
        """The page's name for a graphics state that fills at `opacity`, written to the page.

        Rounded to a thousandth and named for it, so writing it again changes nothing.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        permille = round(opacity * _PERMILLE)
        name = f"{_OPACITY_PREFIX}{permille}"
        resources = _page_resources(mu.pdf_lookup_page_obj(pdf, page))
        states = _dictionary_under(resources, mu.PDF_ENUM_NAME_ExtGState)
        state = mu.pdf_new_dict(pdf, 1)
        mu.pdf_dict_put_real(state, mu.PDF_ENUM_NAME_ca, permille / _PERMILLE)
        mu.pdf_dict_puts(states, name, state)
        return name

    def add_opacity_as_pymupdf(self, page: int, opacity: float) -> str:
        """The page's name for a graphics state filling at `opacity`, as `insert_text` adds it.

        Named as PyMuPDF names it, for `opacity` to a hundredth, and stroking solid. One the
        page already has under that name is used as it is, so the bytes are PyMuPDF's.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        name = _FILL_OPACITY_NAME.format(min(round(opacity * _PERCENT), _PERCENT - 1))
        resources = _page_resources(mu.pdf_lookup_page_obj(pdf, page))
        states = _dictionary_under(resources, mu.PDF_ENUM_NAME_ExtGState)
        # Already there: an empty m_internal means it isn't.
        if mu.pdf_dict_gets(states, name).m_internal:
            return name
        state = mu.pdf_new_dict(pdf, 2)
        mu.pdf_dict_put_real(state, mu.PDF_ENUM_NAME_CA, SOLID)
        mu.pdf_dict_put_real(state, mu.PDF_ENUM_NAME_ca, opacity)
        mu.pdf_dict_puts(states, name, state)
        return name

    def add_drawing(self, page: int, drawing: bytes) -> None:
        """Add `drawing` last in the page's list of drawings (its Contents), so it's on top.

        Added to the page's own list, not by writing the list out again: each edit adds one.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        added = mu.pdf_add_stream(
            pdf, mu.fz_new_buffer_from_copied_data(drawing), mu.PdfObj(), 0
        )
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        # A list of the page's own, written in place: the drawing goes last.
        if mu.pdf_is_array(contents) and not mu.pdf_is_indirect(contents):
            mu.pdf_array_push(contents, added)
            return
        listed = _own_list_of(pdf, contents)
        mu.pdf_array_push(listed, added)
        mu.pdf_dict_put(page_obj, mu.PDF_ENUM_NAME_Contents, listed)

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page, read unturned, into the PDF's own coordinates.

        Not page.transformation_matrix: on a turned page it forgets where the
        page's box starts.
        """
        moved = pymupdf.Point(*point) * to_pdf_matrix(self.doc, page)
        return (moved.x, moved.y)


def _own_list_of(
    pdf: pymupdf.mupdf.PdfDocument, contents: pymupdf.mupdf.PdfObj
) -> pymupdf.mupdf.PdfObj:
    """A new list, written in place, of the drawings a page's Contents names."""
    mu = pymupdf.mupdf
    # A list kept apart, which another page may share: a copy, the page's alone.
    if mu.pdf_is_array(contents):
        return mu.pdf_copy_array(contents)
    listed = mu.pdf_new_array(pdf, 1)
    # One drawing, listed first; an empty m_internal means none.
    if contents.m_internal:
        mu.pdf_array_push(listed, contents)
    return listed


def _page_resources(page: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    """The resources `page` draws with, its own or inherited, made empty if it has none."""
    mu = pymupdf.mupdf
    resources = mu.pdf_dict_get_inheritable(page, mu.PDF_ENUM_NAME_Resources)
    # An empty m_internal means the key isn't there yet, so make it.
    if not resources.m_internal:
        return mu.pdf_dict_put_dict(page, mu.PDF_ENUM_NAME_Resources, 1)
    return resources


def _dictionary_under(
    holder: pymupdf.mupdf.PdfObj, key: pymupdf.mupdf.PdfObj
) -> pymupdf.mupdf.PdfObj:
    """The dictionary `holder` keeps under the name `key`, made empty if it has none."""
    mu = pymupdf.mupdf
    found = mu.pdf_dict_get(holder, key)
    # An empty m_internal means the key isn't there yet, so make it.
    if not found.m_internal:
        return mu.pdf_dict_put_dict(holder, key, 1)
    return found
