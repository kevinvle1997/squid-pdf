"""The seam another PDF library would fill: PyMuPDF is AGPL, pypdfium2 and pikepdf aren't."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from squidpdf.core.app.message import Message
from squidpdf.core.types import (
    CodeRun,
    DrawnGlyph,
    Face,
    FontCode,
    FontDescriptor,
    FontResource,
    FormField,
    HiddenCopy,
    Page,
    PageFont,
    PlacedLetter,
    QuarterTurn,
    Rect,
    TextPiece,
    TextRun,
)


class DriverError(Exception):
    def __init__(self, reason: Message, *, debug: str = "") -> None:
        super().__init__(reason, debug)
        self.reason = reason
        self.debug = debug


class FontProgram(Protocol):
    def listed_letters(self) -> list[int]: ...

    def advance(self, ch: str) -> float: ...

    def maps(self, ch: str) -> bool: ...

    def width(self, text: str, size: float) -> float: ...


class PdfDriver(Protocol):
    """Pages count from 0; boxes are in points, top-left origin, on the page unrotated."""

    def page_count(self) -> int: ...

    def pages(self) -> list[Page]: ...

    def page_image(self, page: int, scale: float) -> bytes: ...

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]: ...

    def text_lines(self, page: int) -> list[list[TextPiece]]: ...

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]: ...

    def letters_in(self, page: int, boxes: list[Rect]) -> list[list[PlacedLetter]]: ...

    def drawn_boxes(self, page: int) -> list[Rect]: ...

    def link_boxes(self, page: int) -> list[Rect]: ...

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]: ...

    def drawn_glyph_boxes(self, page: int) -> list[Rect] | None: ...

    def form_fields(self, page: int) -> list[FormField]: ...

    def fonts(self, page: int) -> list[PageFont]: ...

    def font_bytes(self, font_object: int) -> bytes: ...

    def font_descriptor(self, font_object: int) -> FontDescriptor | None: ...

    def font_codes(self, font_object: int, code_bytes: int) -> list[FontCode] | None: ...

    def listed_widths(self, font_object: int) -> dict[str, float] | None: ...

    def open_font(self, font_file: bytes) -> FontProgram: ...

    def face_font(self, face: Face) -> FontProgram: ...

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        """Returns what `text_in` still reads in each box; a FreeText over them goes too."""
        ...

    def drop_links(
        self, page: int, boxes: list[Rect], *, holding: Callable[[str], bool] | None = None
    ) -> None: ...

    def hidden_copies(self, page: int) -> list[str]: ...

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        """A hidden copy: text marked content keeps beside its drawing (ActualText, Alt, E)."""
        ...

    def document_hidden_copies(self, holding: Callable[[str], bool]) -> list[HiddenCopy]: ...

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None: ...

    def holds_signature(self) -> bool: ...

    def is_signed(self) -> bool: ...

    def drop_signatures(self) -> None:
        """Nothing in them is read: the edit rewrites the file, so none would still hold."""
        ...

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource: ...

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
        letter_spacing: float,
    ) -> None: ...

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
        letter_spacing: float,
    ) -> None: ...

    def replace_font_file(self, font_object: int, font_file: bytes) -> None:
        """The new file must keep each glyph at its old number."""
        ...

    def drop_active_content(self) -> bool: ...

    def keeps_xmp_plain(self) -> bool: ...

    def drop_xmp(self) -> None: ...

    def has_tags(self) -> bool: ...

    def drop_tags(self) -> None: ...

    def keep_pages(self, pages: list[int]) -> None: ...

    def turn_page(self, page: int, turn_cw: QuarterTurn) -> None: ...

    def mark_changed_by(self, producer: str, when: datetime) -> None: ...

    def save(self, path: str) -> None: ...

    def close(self) -> None: ...


type DriverFactory = Callable[[str], PdfDriver]
