"""What a page's own drawing writes, glyph by glyph: its comments' and fields' aside."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pymupdf

from squidpdf.core.pdf.lowlevel.pages import unturned


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
