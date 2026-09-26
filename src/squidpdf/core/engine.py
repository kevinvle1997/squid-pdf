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
from squidpdf.core.types import Page, Rect, Span, SpanIndex


@runtime_checkable
class Engine(Protocol):
    """Everything the app asks of a PDF library. `MuPDFEngine` is the one there is."""

    def index(self) -> SpanIndex:
        """Every editable span, extracted once from the pristine document."""
        ...

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        ...

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box."""
        ...

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Whether each span can be edited in its own font."""
        ...

    def glyphs(self, span: Span) -> dict[str, float]:
        """Each character this span's drawing font really draws, to its width per 1000 em."""
        ...

    def measure(self, span: Span, text: str) -> float:
        """Rendered width in points, in the face `draw` would use for this text."""
        ...

    def left_out(self, span: Span, text: str) -> list[str]:
        """Characters no font we have can draw here, so a redraw leaves them out."""
        ...

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters this span's drawing font cannot draw, substitute or not."""
        ...

    def stand_in(self, span: Span, text: str) -> str:
        """The face we ship that draws `text` when the span's own font can't."""
        ...

    def remove(self, spans: list[Span]) -> None:
        """Delete these glyph runs. Real deletion, not a covering rectangle."""
        ...

    def draw(
        self, span: Span, text: str, size: float | None = None, scale_x: float = 1.0
    ) -> list[str]:
        """Redraw at the span's baseline, in its own font where it draws every character.

        `size` in points replaces the span's own; `scale_x` narrows it horizontally.
        Returns, in plain words, anything that came out other than asked.
        """
        ...

    def save(self, path: str) -> list[str]:
        """Write the document to `path`. Returns anything that came out other than asked."""
        ...

    def absent(self, text: str) -> bool:
        """Confirm a removed string is really gone. Verified redaction depends on this."""
        ...

    def close(self) -> None:
        """Release the open document."""
        ...
