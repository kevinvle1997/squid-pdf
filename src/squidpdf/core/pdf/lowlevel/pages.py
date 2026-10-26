"""A page as MuPDF's low-level API sees it: unturned, and its points in the PDF's own space."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pymupdf


def to_pdf_matrix(doc: pymupdf.Document, page: int) -> pymupdf.Matrix:
    """What turns a point on the page, read unturned, into the PDF's own coordinates."""
    mu = pymupdf.mupdf
    with unturned(doc, page) as pdf_page:
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(pdf_page, _mediabox, page_to_screen)
    return ~pymupdf.Matrix(page_to_screen)


@contextmanager
def unturned(doc: pymupdf.Document, page: int) -> Iterator[pymupdf.mupdf.PdfPage]:
    """The page with its turn (its Rotate) taken off inside the `with`, put back after.

    Not PyMuPDF's rotation_matrix: it turns from the whole crop box, MuPDF from the part on
    the paper (the MediaBox), so they differ on a page cropped past its paper.
    """
    pg = doc[page]
    turn_cw = pg.rotation
    # A page not turned is left as it is, with no Rotate written.
    if turn_cw:
        pg.set_rotation(0)
    try:
        yield pymupdf.mupdf.pdf_page_from_fz_page(pg.this)
    finally:
        if turn_cw:
            pg.set_rotation(turn_cw)
