from __future__ import annotations

import pymupdf

# MuPDF's own errors aren't RuntimeErrors; PyMuPDF adds RuntimeErrors and ValueErrors.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)

# These hold a pointer, so they can't cross between processes.
MUPDF_OWN_ERRORS = pymupdf.mupdf.FzErrorBase

MUPDF_TOO_HEAVY = pymupdf.mupdf.FzErrorLimit

MUPDF_SYSTEM_ERRORS = pymupdf.mupdf.FzErrorSystem
