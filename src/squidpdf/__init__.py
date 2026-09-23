"""squid-pdf — correct text already in a PDF, and know beforehand how it will look."""

from squidpdf.core import Fidelity, MuPDFEngine, Span, SpanIndex, green_rate
from squidpdf.editing import Edit, EditLog, Redact, Replace

__all__ = [
    "MuPDFEngine",
    "Span",
    "SpanIndex",
    "Fidelity",
    "green_rate",
    "Edit",
    "EditLog",
    "Replace",
    "Redact",
]
__version__ = "0.1.0"
