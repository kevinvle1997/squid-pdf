"""What the engine asks of a PDF library: the seam another library would fill.

Primitives only: read what the file says, change it, draw on it, save it. What
to make of it (spans, fidelity, stand-ins, fits) is `core.engine`'s, the same
over any backend. PyMuPDF is AGPL; a permissive rewrite would implement these
two protocols over pypdfium2 and pikepdf, and nothing else.
"""

from __future__ import annotations

from typing import Protocol

from squidpdf.core.types import (
    Face,
    FontCode,
    FontDescriptor,
    Page,
    PageFont,
    Rect,
    TextPiece,
)


class FontProgram(Protocol):
    """A font file the library has opened, to measure with."""

    def claimed(self) -> list[int]:
        """Every code point the library says the font maps; a trimmed font claims more."""
        ...

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        ...

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points."""
        ...


class Backend(Protocol):
    """A PDF open in a library. `core.mupdf.MuPDFBackend` is the one there is.

    Pages count from 0. Boxes and points are in points, top-left origin, on the
    page unrotated. Where a method says it raises ValueError, that is how it
    says the library couldn't: the engine then falls back rather than crashing.
    """

    def pages(self) -> list[Page]:
        """Each page's size, unrotated, and the turn it asks for."""
        ...

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box."""
        ...

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in."""
        ...

    def text_in(self, page: int, box: Rect) -> str:
        """The letters drawn inside `box` on the page, in reading order."""
        ...

    def fonts(self, page: int) -> list[PageFont]:
        """Every font the page uses, including inside forms."""
        ...

    def font_bytes(self, xref: int) -> bytes | None:
        """The font file stored in the PDF, or None if the library can't read it out."""
        ...

    def font_descriptor(self, xref: int) -> FontDescriptor | None:
        """What the font's description says about how it looks; None when it has none."""
        ...

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first; None without a letter list.

        Raises ValueError when the library can't load the font.
        """
        ...

    def open_font(self, font_file: bytes) -> FontProgram:
        """Open a font file to measure with. Raises ValueError when the library can't."""
        ...

    def face_font(self, face: Face) -> FontProgram:
        """A face we ship, opened to measure with: it measures what `add_font` draws."""
        ...

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Delete the text inside these boxes for real. Images and drawings stay."""
        ...

    def add_font(self, page: int, name: str, font_file: bytes) -> int:
        """Add a font to the page under `name`; returns its object number.

        Raises ValueError when the library won't add it.
        """
        ...

    def write_text(
        self,
        page: int,
        origin: tuple[float, float],
        text: str,
        font: str,
        size: float,
        color: tuple[float, float, float],
        scale_x: float,
    ) -> None:
        """Write `text` from `origin` on its baseline, in the font the page calls `font`.

        On top of everything on the page; `scale_x` narrows it from its start.
        """
        ...

    def restore_font(self, page: int, resource: str, xref: int) -> None:
        """Point the page's font name `resource` back at font `xref`."""
        ...

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page as you see it into the PDF's own coordinates."""
        ...

    def add_content(self, page: int, stream: bytes) -> None:
        """Draw a content stream on top of everything on the page."""
        ...

    def replace_font_file(self, xref: int, font_file: bytes) -> None:
        """Swap in a new file for font `xref`. It must keep each glyph at its old number."""
        ...

    def save(self, path: str) -> None:
        """Write the document to `path`."""
        ...

    def close(self) -> None:
        """Release the open document."""
        ...
