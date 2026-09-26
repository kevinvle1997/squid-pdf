"""What the PDF says, and what we can promise about changing it.

Imported by every feature. Nothing here may import a feature, and nothing
outside `core` names the PDF library: open a PDF with `open_pdf`.
tests/test_layers.py checks both.
"""

from squidpdf.core.constants import GREEN_RATE_TARGET, GREEN_RATE_WARN
from squidpdf.core.engine import Engine
from squidpdf.core.errors import Damaged, Encrypted, NotFound, Problem, Unreadable
from squidpdf.core.fidelity import Fidelity, FidelityReport, green_rate

# The one line that names the backend: another PDF library is swapped in here.
from squidpdf.core.mupdf import BUILD, face_widths, open_pdf, write_sample
from squidpdf.core.types import Fragment, Page, Rect, Span, SpanIndex, new_text

__all__ = [
    "BUILD",
    "Engine",
    "open_pdf",
    "write_sample",
    "Problem",
    "NotFound",
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
    "Rect",
    "Span",
    "SpanIndex",
    "new_text",
]
