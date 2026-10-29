"""What a page's own drawing writes, glyph by glyph: its comments' and fields' aside."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pymupdf

from squidpdf.core.pdf.lowlevel.pages import unturned

# A glyph's box: left, top, right, bottom, on the page as it's stored.
type GlyphBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True, eq=False)
class PageGlyphs:
    """The glyphs a page's drawing writes, wherever they land, on the page or off it."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def drawn(self, page: int) -> list[dict[str, Any]]:
        """Each run of glyphs the page's own drawing writes, as PyMuPDF's text trace reads one.

        Its contents alone: a comment or a field draws its own, which edits to it redraw.
        """
        mu = pymupdf.mupdf
        runs: list[dict[str, Any]] = []
        # Unturned, as PyMuPDF reads a text trace: places on the page as it's stored.
        with unturned(self.doc, page):
            drawn = self.doc[page].this
            device = pymupdf.extra.JM_new_texttrace_device(runs)
            device.ptm = mu.FzMatrix(1, 0, 0, -1, 0, mu.fz_bound_page(drawn).y1)
            mu.fz_run_page_contents(drawn, device, mu.FzMatrix(), mu.FzCookie())
            mu.fz_close_device(device)
        return runs

    def boxes(self, page: int) -> list[GlyphBox]:
        """The box of each glyph `drawn` reads, in its order: the glyph's ink, as drawn.

        Drawn: through all of its text's matrix, so a letter condensed or stretched up is as
        tall as it shows. The text trace sizes a letter by its width alone, and every letter
        of a font alike. A glyph that draws nothing has an empty box where it starts.
        """
        mu = pymupdf.mupdf
        boxes: list[GlyphBox] = []
        with unturned(self.doc, page):
            drawn = self.doc[page].this
            device = _GlyphBoxes(boxes)
            mu.fz_run_page_contents(drawn, device, mu.FzMatrix(), mu.FzCookie())
            mu.fz_close_device(device)
        return boxes


# A MuPDF device is made by deriving from its class: the library calls back what it overrides.
@dataclass(slots=True, eq=False)
class _GlyphBoxes(pymupdf.mupdf.FzDevice2):
    """A device noting each glyph's box, for the text the text trace reads, and only that.

    Filled, stroked or drawn invisible, as the text trace reads it; a clip draws nothing.
    """

    boxes: list[GlyphBox]  # each glyph's, in the order drawn, added to as the page is run

    def __post_init__(self) -> None:
        super().__init__()
        self.use_virtual_fill_text()
        self.use_virtual_stroke_text()
        self.use_virtual_ignore_text()

    def fill_text(self, _ctx: Any, text: Any, ctm: Any, *_paint: Any) -> None:
        """Filled text's glyphs."""
        self._note(text, ctm)

    def stroke_text(self, _ctx: Any, text: Any, _stroke: Any, ctm: Any, *_paint: Any) -> None:
        """Stroked text's glyphs."""
        self._note(text, ctm)

    def ignore_text(self, _ctx: Any, text: Any, ctm: Any) -> None:
        """Invisible text's glyphs."""
        self._note(text, ctm)

    def _note(self, text: Any, ctm: Any) -> None:
        """Each glyph of `text`'s runs, drawn through `ctm`."""
        mu = pymupdf.mupdf
        page_matrix = mu.FzMatrix(ctm)
        run = text.head
        while run:
            each = mu.FzTextSpan(run)
            font, trm = each.font(), each.trm()
            for at in range(each.m_internal.len):
                item = each.items(at)
                if item.gid < 0:
                    origin = mu.fz_transform_point(
                        mu.fz_make_point(item.x, item.y), page_matrix
                    )
                    self.boxes.append((origin.x, origin.y, origin.x, origin.y))
                    continue
                placed = mu.FzMatrix(trm.a, trm.b, trm.c, trm.d, item.x, item.y)
                box = mu.fz_bound_glyph(font, item.gid, mu.fz_concat(placed, page_matrix))
                self.boxes.append((box.x0, box.y0, box.x1, box.y1))
            run = run.next
