"""The narrow seam everything else is written against.

Keeping this small is what makes the engine replaceable. PyMuPDF is AGPL; if the
project ever needs a permissive licence, only this gets reimplemented against
pypdfium2 and pikepdf, not the app.

Two rules hold this in place. Nothing outside `core` imports PyMuPDF. And the
engine speaks only in primitives — remove, draw — so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from squidpdf.core.fidelity import FidelityReport
from squidpdf.core.types import Span, SpanIndex


@runtime_checkable
class Engine(Protocol):
    def index(self) -> SpanIndex:
        """Every editable span, extracted once from the pristine document."""
        ...

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Whether each span can be edited in its own font."""
        ...

    def measure(self, span: Span, text: str) -> float:
        """Rendered width in points, in the face this span would actually use."""
        ...

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's font cannot draw."""
        ...

    def remove(self, spans: list[Span]) -> None:
        """Delete these glyph runs. Real deletion, not a covering rectangle."""
        ...

    def draw(self, span: Span, text: str) -> None:
        """Redraw at the span's baseline, in its own font where the file has it."""
        ...

    def draw_at(
        self,
        page: int,
        origin: tuple[float, float],
        text: str,
        size: float,
        color: tuple[float, float, float] = ...,
    ) -> None:
        """Draw where the document has no text."""
        ...

    def save(self, path: str) -> None: ...

    def absent(self, text: str) -> bool:
        """Confirm a removed string is really gone. Verified redaction depends on this."""
        ...

    def close(self) -> None: ...
