"""What the PDF says, and what we can promise about changing it.

Imported by every feature. Nothing here may import a feature. That rule is
convention, not enforced, so watch it in review.
"""

from squidpdf.core.constants import GREEN_RATE_TARGET, GREEN_RATE_WARN
from squidpdf.core.engine import Engine, Unreadable
from squidpdf.core.fidelity import Fidelity, FidelityReport, green_rate
from squidpdf.core.mupdf import BUILD, MuPDFEngine
from squidpdf.core.types import Fragment, Page, Rect, Span, SpanIndex

__all__ = [
    "BUILD",
    "Engine",
    "Unreadable",
    "MuPDFEngine",
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
]
