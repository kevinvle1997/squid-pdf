"""What the PDF says, and what we can promise about changing it.

Imported by every feature. Nothing here may import a feature. That rule is
convention, not enforced, so watch it in review.
"""

from squidpdf.core.engine import Engine
from squidpdf.core.fidelity import (
    GREEN_RATE_TARGET,
    GREEN_RATE_WARN,
    Fidelity,
    FidelityReport,
    green_rate,
)
from squidpdf.core.mupdf import MuPDFEngine
from squidpdf.core.types import Fragment, Rect, Span, SpanIndex

__all__ = [
    "Engine",
    "MuPDFEngine",
    "Fidelity",
    "FidelityReport",
    "green_rate",
    "GREEN_RATE_TARGET",
    "GREEN_RATE_WARN",
    "Fragment",
    "Rect",
    "Span",
    "SpanIndex",
]
