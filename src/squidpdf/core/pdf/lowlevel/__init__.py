"""The only package that uses MuPDF's low-level API, one module per concern.

Everyday PyMuPDF calls (get_text, get_pixmap, save) are easy to read, so they stay in
`core.pdf.mupdf`, whose driver holds a `PdfFile` for the rest. Results come back as named
dataclasses, reporting only what the PDF says: the engine decides what to do.
"""

from __future__ import annotations

from squidpdf.core.pdf.lowlevel.errors import (
    MUPDF_ERRORS as MUPDF_ERRORS,
    MUPDF_OWN_ERRORS as MUPDF_OWN_ERRORS,
    MUPDF_SYSTEM_ERRORS as MUPDF_SYSTEM_ERRORS,
    MUPDF_TOO_HEAVY as MUPDF_TOO_HEAVY,
)
from squidpdf.core.pdf.lowlevel.file import PdfFile as PdfFile, pdf_file as pdf_file
from squidpdf.core.pdf.lowlevel.objects import PDF_NULL as PDF_NULL
