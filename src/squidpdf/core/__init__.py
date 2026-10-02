"""What the PDF says, and what we can promise about changing it.

Imported by every feature, through this module only: nothing outside `core`
imports one of its modules, so what a feature uses is exported here on purpose,
and nothing outside names the PDF library (open a PDF with `open_pdf`). Nothing
here may import a feature. tests/test_layers.py checks all three.
"""

from __future__ import annotations

from squidpdf.core.app import words
from squidpdf.core.app.errors import (
    Damaged,
    Encrypted,
    ErrorController,
    Failure,
    InvalidRequest,
    NotFound,
    Problem,
    TooHeavy,
    Unreadable,
)
from squidpdf.core.app.message import Message, MessageInfo, Param, SaidInfo
from squidpdf.core.app.reply import Reply
from squidpdf.core.app.workers import Workers
from squidpdf.core.constants import (
    CONDENSE_LIMIT,
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    OPTION_KEYS,
    SHRINK_FLOOR,
    TOLERANCE_PT,
)
from squidpdf.core.engine import Engine, LineToDraw
from squidpdf.core.fonts.catalog import CATALOG, FACES
from squidpdf.core.fonts.document import FontSources
from squidpdf.core.fonts.google import google_fonts

# The one import that names the driver: another PDF library is swapped in here.
from squidpdf.core.pdf.mupdf import BUILD, CORE_ERRORS, face_widths, open_pdf, result_of
from squidpdf.core.pdf.samples import write_dense, write_sample
from squidpdf.core.text.fidelity import (
    APPROXIMATE_REASONS,
    ApproximateReason,
    Fidelity,
    FidelityReport,
    green_rate,
    reason_of,
)
from squidpdf.core.types import (
    LEVEL,
    SOLID,
    Category,
    Fragment,
    Page,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    Style,
    index_of,
    new_text,
)

__all__ = [
    "BUILD",
    "Engine",
    "LineToDraw",
    "open_pdf",
    "FontSources",
    "result_of",
    "google_fonts",
    "write_sample",
    "write_dense",
    "Message",
    "MessageInfo",
    "SaidInfo",
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
    "Failure",
    "CORE_ERRORS",
    "face_widths",
    "Fidelity",
    "FidelityReport",
    "ApproximateReason",
    "APPROXIMATE_REASONS",
    "reason_of",
    "green_rate",
    "GREEN_RATE_TARGET",
    "GREEN_RATE_WARN",
    "TOLERANCE_PT",
    "CONDENSE_LIMIT",
    "SHRINK_FLOOR",
    "OPTION_KEYS",
    "CATALOG",
    "FACES",
    "Category",
    "Style",
    "Fragment",
    "Page",
    "QuarterTurn",
    "LEVEL",
    "SOLID",
    "Rect",
    "Span",
    "SpanIndex",
    "index_of",
    "new_text",
    "Workers",
    "words",
]
