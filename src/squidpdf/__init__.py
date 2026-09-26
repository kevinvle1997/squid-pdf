"""squid-pdf: correct text already in a PDF, and know beforehand how it will look."""

from squidpdf.core import Engine, Fidelity, Span, SpanIndex, green_rate, open_pdf
from squidpdf.editing import Edit, EditLog, Redact, Replace

__all__ = [
    "open_pdf",
    "Engine",
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
