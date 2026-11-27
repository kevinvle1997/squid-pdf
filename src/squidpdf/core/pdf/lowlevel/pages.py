from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pymupdf


def to_pdf_matrix(doc: pymupdf.Document, page: int) -> pymupdf.Matrix:
    mu = pymupdf.mupdf
    with unturned(doc, page) as pdf_page:
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(pdf_page, _mediabox, page_to_screen)
    return ~pymupdf.Matrix(page_to_screen)


@contextmanager
def unturned(doc: pymupdf.Document, page: int) -> Iterator[pymupdf.mupdf.PdfPage]:
    """Not PyMuPDF's rotation_matrix: it turns from the crop box, MuPDF from the MediaBox."""
    pg = doc[page]
    turn_cw = pg.rotation
    if turn_cw:
        pg.set_rotation(0)
    try:
        yield pymupdf.mupdf.pdf_page_from_fz_page(pg.this)
    finally:
        if turn_cw:
            pg.set_rotation(turn_cw)
