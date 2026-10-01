"""What the engine asks of a PDF library: the seam another library would fill.

Primitives only: read what the file says, change it, draw on it, save it. What
to make of it (spans, fidelity, substitutes, fits) is `core.engine`'s, the same
over any driver. PyMuPDF is AGPL; a permissive rewrite would implement these
two protocols over pypdfium2 and pikepdf, and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from squidpdf.core.app.message import Message
from squidpdf.core.types import (
    CodeRun,
    Face,
    FontCode,
    FontDescriptor,
    FontResource,
    Page,
    PageFont,
    QuarterTurn,
    Rect,
    TextPiece,
    TextRun,
)


class DriverError(Exception):
    """The library couldn't do what was asked with a font, and why, for the edge to say.

    One error for every way a font fails in the driver, as `FontUnusable` is in
    the engine. Caught by name.
    """

    def __init__(self, reason: Message, *, debug: str = "") -> None:
        """`reason` names a sentence in `core.app.words`; `debug` is the library's own words."""
        super().__init__(reason, debug)
        self.reason = reason
        self.debug = debug


class FontProgram(Protocol):
    """A font file the library has opened, to measure with."""

    def listed_letters(self) -> list[int]:
        """Every code point the library says the font maps; a trimmed font lists more."""
        ...

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        ...

    def maps(self, ch: str) -> bool:
        """Whether the font has a glyph of its own for `ch`, even an empty one."""
        ...

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points."""
        ...


class PdfDriver(Protocol):
    """A PDF open in a library. `core.pdf.mupdf.MuPDFDriver` is the one there is.

    Pages count from 0. Boxes and points are in points, top-left origin, on the
    page unrotated. A font the library can't use raises DriverError, saying
    why, and the engine falls back rather than crashing. Anything else the
    library raises goes up to `result_of`, which says what it means.
    """

    def page_count(self) -> int:
        """How many pages the document has, without reading any of them."""
        ...

    def pages(self) -> list[Page]:
        """Each page's size, unrotated, and the turn it asks for."""
        ...

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box."""
        ...

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in."""
        ...

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]:
        """The letters inside each box on the page, in reading order."""
        ...

    def fonts(self, page: int) -> list[PageFont]:
        """Every font the page uses, including inside forms."""
        ...

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` reads, often the font file's own; None if unreadable."""
        ...

    def font_bytes(self, xref: int) -> bytes:
        """The font file stored in the PDF. Raises DriverError when it can't be read out."""
        ...

    def font_descriptor(self, xref: int) -> FontDescriptor | None:
        """What the font's description says about how it looks; None when it has none."""
        ...

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first; None without a letter list.

        Raises DriverError when the library can't load the font.
        """
        ...

    def open_font(self, font_file: bytes) -> FontProgram:
        """Open a font file to measure with. Raises DriverError when it isn't one."""
        ...

    def face_font(self, face: Face) -> FontProgram:
        """A face we ship, opened to measure with: it measures what `add_font` draws."""
        ...

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        """Delete the letters whose middle is inside these boxes, for real.

        Returns the letters still inside each box, as `text_in` reads them: those
        it couldn't reach, as a form field draws its value, not the page. Images,
        drawings and links stay, and so does every font the driver named on the
        page, under the same resource name: each `add_font` put there, and each of
        the file's own a `write_codes` named again. Any other font of the file's
        own that no text on the page uses any more may go.
        """
        ...

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`."""
        ...

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """Add a font to the page under the resource name `resource`, or one like it if taken.

        Returns the resource name it went under, and its PDF object. Raises
        DriverError when the library won't add it.
        """
        ...

    def write_text(
        self,
        page: int,
        *,
        runs: Sequence[TextRun],
        size: float,
        color: tuple[float, float, float],
        opacity: float,
        scale_x: float,
        turn_ccw: QuarterTurn,
    ) -> None:
        """Write each run from its origin, in its font, on top of the page, in order.

        `scale_x` narrows each run from its own start; an `opacity` of 1 is solid.
        `turn_ccw` turns each run counter-clockwise about its origin.
        """
        ...

    def write_codes(
        self,
        page: int,
        *,
        origin: tuple[float, float],
        runs: Sequence[CodeRun],
        size: float,
        color: tuple[float, float, float],
        opacity: float,
        scale_x: float,
        turn_ccw: QuarterTurn,
    ) -> None:
        """Write each run's codes in its font, from `origin` on, on top of the page.

        For a font that has no letters of its own, only codes (`CodeRun` says
        what a code is). Each run starts where the last left the pen, moved on
        by that font's own widths. The rest is as for `write_text`.
        """
        ...

    def replace_font_file(self, xref: int, font_file: bytes) -> None:
        """Swap in a new file for font `xref`. It must keep each glyph at its old number.

        Raises DriverError when the font's file can't be found to swap.
        """
        ...

    def has_tags(self) -> bool:
        """Whether the file is tagged: it has the reading order a screen reader follows."""
        ...

    def drop_tags(self) -> None:
        """Remove the file's tags. Saving then drops every page only they pointed at."""
        ...

    def keep_pages(self, pages: list[int]) -> None:
        """Keep only `pages`, in that order; links, bookmarks and fields on the rest go too."""
        ...

    def save(self, path: str) -> None:
        """Write the document to `path`."""
        ...

    def close(self) -> None:
        """Release the open document."""
        ...
