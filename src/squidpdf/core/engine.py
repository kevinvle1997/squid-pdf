"""The narrow seam everything else is written against.

Keeping this small is what makes the engine replaceable. PyMuPDF is AGPL; if the
project ever needs a permissive licence, only this gets reimplemented against
pypdfium2 and pikepdf, not the app.

Two rules hold this in place. Nothing outside `core` imports PyMuPDF. And the
engine speaks only in primitives (remove, draw), so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from squidpdf.core.fidelity import FidelityReport
from squidpdf.core.types import Rect, Span, SpanIndex


@runtime_checkable
class Engine(Protocol):
    """Everything the app asks of a PDF library. `MuPDFEngine` is the one there is."""

    def index(self) -> SpanIndex:
        """Every editable span, extracted once from the pristine document."""
        ...

    def pages(self) -> list[tuple[float, float]]:
        """Each page's width and height in points, as displayed: rotation applied."""
        ...

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page as a PNG, `scale` pixels per point, or only the `clip` box of it."""
        ...

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Whether each span can be edited in its own font."""
        ...

    def glyphs(self, span: Span) -> dict[str, float]:
        """Every character this span's drawing font really draws, to its advance per 1000 em."""
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

    def save(self, path: str) -> None:
        """Write the document, edits and all, to `path`."""
        ...

    def absent(self, text: str) -> bool:
        """Confirm a removed string is really gone. Verified redaction depends on this."""
        ...

    def close(self) -> None:
        """Release the open document."""
        ...
