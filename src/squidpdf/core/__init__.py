"""What the PDF says, and what we can promise about changing it.

Imported by every feature. Nothing here may import a feature, and nothing
outside `core` names the PDF library: open a PDF with `open_pdf`.
tests/test_layers.py checks both.
"""

from __future__ import annotations

from squidpdf.core.constants import GREEN_RATE_TARGET, GREEN_RATE_WARN
from squidpdf.core.engine import Engine
from squidpdf.core.errors import (
    Damaged,
    Encrypted,
    InvalidRequest,
    NotFound,
    Problem,
    Unreadable,
)
from squidpdf.core.fidelity import Fidelity, FidelityReport, green_rate
from squidpdf.core.message import Message, MessageInfo, Param

# The one line that names the driver: another PDF library is swapped in here.
from squidpdf.core.mupdf import BUILD, face_widths, open_pdf, write_sample
from squidpdf.core.reply import Reply
from squidpdf.core.types import LEVEL, SOLID, Fragment, Page, Rect, Span, SpanIndex, new_text
from squidpdf.core.workers import Workers

__all__ = [
    "BUILD",
    "Engine",
    "open_pdf",
    "write_sample",
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
]
