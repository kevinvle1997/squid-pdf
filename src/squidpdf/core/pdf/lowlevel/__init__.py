"""The only package that uses MuPDF's low-level API; everyday PyMuPDF calls stay in `mupdf`."""

from __future__ import annotations

from squidpdf.core.pdf.lowlevel.errors import (
    MUPDF_ERRORS as MUPDF_ERRORS,
    MUPDF_OWN_ERRORS as MUPDF_OWN_ERRORS,
    MUPDF_SYSTEM_ERRORS as MUPDF_SYSTEM_ERRORS,
    MUPDF_TOO_HEAVY as MUPDF_TOO_HEAVY,
)
from squidpdf.core.pdf.lowlevel.file import PdfFile as PdfFile, pdf_file as pdf_file
from squidpdf.core.pdf.lowlevel.objects import (
    PDF_NULL as PDF_NULL,
    PDF_REFERENCE as PDF_REFERENCE,
    object_number as object_number,
)
