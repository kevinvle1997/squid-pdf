"""squid-pdf: correct text already in a PDF, and know beforehand how it will look."""

from __future__ import annotations

from squidpdf.core import (
    Engine as Engine,
    Fidelity as Fidelity,
    Span as Span,
    SpanIndex as SpanIndex,
    green_rate as green_rate,
    open_pdf as open_pdf,
)
from squidpdf.editing import Edit as Edit, Redact as Redact, Replace as Replace

__version__ = "0.1.0"
