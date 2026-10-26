"""MuPDF's failures, as the exception types PyMuPDF raises them with."""

from __future__ import annotations

import pymupdf

# What PyMuPDF raises when MuPDF can't do what it was asked: MuPDF's own errors,
# which aren't RuntimeErrors, and the RuntimeErrors and ValueErrors PyMuPDF adds.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)

# MuPDF's own errors alone, which hold a pointer, so they can't cross between processes.
MUPDF_OWN_ERRORS = pymupdf.mupdf.FzErrorBase

# The one of them that means the work was too big, not the file broken: past MuPDF's limit.
MUPDF_TOO_HEAVY = pymupdf.mupdf.FzErrorLimit

# The one that means the machine failed, not the file: out of memory, or a file it can't open.
MUPDF_SYSTEM_ERRORS = pymupdf.mupdf.FzErrorSystem
