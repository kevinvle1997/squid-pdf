"""What the PDF says, and what we can promise about changing it.

Imported by every feature. Nothing here may import a feature, and nothing
outside `core` names the PDF library: open a PDF with `open_pdf`.
tests/test_layers.py checks both.
"""

from __future__ import annotations

from squidpdf.core.app import words
from squidpdf.core.app.errors import (
    Damaged,
    Encrypted,
    ErrorController,
    InvalidRequest,
    NotFound,
    Problem,
    TooHeavy,
    Unreadable,
)
from squidpdf.core.app.message import Message, MessageInfo, Param
from squidpdf.core.app.reply import Reply
from squidpdf.core.app.workers import Workers
from squidpdf.core.constants import GREEN_RATE_TARGET, GREEN_RATE_WARN
from squidpdf.core.engine import Engine
from squidpdf.core.fonts.google import google_fonts

# The one import that names the driver: another PDF library is swapped in here.
from squidpdf.core.pdf.mupdf import BUILD, face_widths, open_pdf, result_of
from squidpdf.core.pdf.samples import write_dense, write_sample
from squidpdf.core.text.fidelity import Fidelity, FidelityReport, green_rate
from squidpdf.core.types import LEVEL, SOLID, Fragment, Page, Rect, Span, SpanIndex, new_text

__all__ = [
    "BUILD",
    "Engine",
    "open_pdf",
    "result_of",
    "google_fonts",
    "write_sample",
    "write_dense",
    "Message",
    "MessageInfo",
    "Param",
    "Problem",
    "Reply",
    "NotFound",
    "InvalidRequest",
    "Unreadable",
    "Encrypted",
    "Damaged",
    "TooHeavy",
    "ErrorController",
    "face_widths",
    "Fidelity",
    "FidelityReport",
    "green_rate",
    "GREEN_RATE_TARGET",
    "GREEN_RATE_WARN",
    "Fragment",
    "Page",
    "LEVEL",
    "SOLID",
    "Rect",
    "Span",
    "SpanIndex",
    "new_text",
    "Workers",
    "words",
]
