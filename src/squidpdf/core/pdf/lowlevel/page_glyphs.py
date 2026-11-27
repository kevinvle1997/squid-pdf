from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pymupdf

from squidpdf.core.pdf.lowlevel.pages import unturned
from squidpdf.core.types import DrawnGlyph, GlyphId

type _GlyphBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True, eq=False)
class PageGlyphs:
    doc: pymupdf.Document

    def drawn(self, page: int) -> list[DrawnGlyph]:
        mu = pymupdf.mupdf
        runs: list[dict[str, Any]] = []
        with unturned(self.doc, page):
            drawn = self.doc[page].this
            device = pymupdf.extra.JM_new_texttrace_device(runs)
            device.ptm = mu.FzMatrix(1, 0, 0, -1, 0, mu.fz_bound_page(drawn).y1)
            mu.fz_run_page_contents(drawn, device, mu.FzMatrix(), mu.FzCookie())
            mu.fz_close_device(device)
        return list(_each_traced_glyph(runs))

    def boxes(self, page: int) -> list[_GlyphBox]:
        """The text trace sizes a letter by its width alone, every letter of a font alike."""
        mu = pymupdf.mupdf
        boxes: list[_GlyphBox] = []
        with unturned(self.doc, page):
            drawn = self.doc[page].this
            device = _GlyphBoxes(boxes)
            mu.fz_run_page_contents(drawn, device, mu.FzMatrix(), mu.FzCookie())
            mu.fz_close_device(device)
        return boxes


def _each_traced_glyph(runs: list[dict[str, Any]]) -> Iterator[DrawnGlyph]:
    for run in runs:
        direction = (run["dir"][0], run["dir"][1])
        for letter, glyph, origin, box in run["chars"]:
            yield DrawnGlyph(
                chr(letter),
                GlyphId(glyph),
                (origin[0], origin[1]),
                box[2] - box[0],
                direction=direction,
            )


@dataclass(slots=True, eq=False)
class _GlyphBoxes(pymupdf.mupdf.FzDevice2):
    boxes: list[_GlyphBox]

    def __post_init__(self) -> None:
        super().__init__()
        self.use_virtual_fill_text()
        self.use_virtual_stroke_text()
        self.use_virtual_ignore_text()

    def fill_text(self, _ctx: Any, text: Any, ctm: Any, *_paint: Any) -> None:
        self._note(text, ctm)

    def stroke_text(self, _ctx: Any, text: Any, _stroke: Any, ctm: Any, *_paint: Any) -> None:
        self._note(text, ctm)

    def ignore_text(self, _ctx: Any, text: Any, ctm: Any) -> None:
        self._note(text, ctm)

    def _note(self, text: Any, ctm: Any) -> None:
        mu = pymupdf.mupdf
        page_matrix = mu.FzMatrix(ctm)
        run = text.head
        while run:
            each = mu.FzTextSpan(run)
            font, trm = each.font(), each.trm()
            self.boxes.extend(
                _box_of(each.items(at), font=font, trm=trm, page_matrix=page_matrix)
                for at in range(each.m_internal.len)
            )
            run = run.next


def _box_of(item: Any, *, font: Any, trm: Any, page_matrix: Any) -> _GlyphBox:
    mu = pymupdf.mupdf
    # A ligature's later letter, whose shape the first one draws: an empty box where it starts.
    if item.gid < 0:
        origin = mu.fz_transform_point(mu.fz_make_point(item.x, item.y), page_matrix)
        return (origin.x, origin.y, origin.x, origin.y)
    placed = mu.FzMatrix(trm.a, trm.b, trm.c, trm.d, item.x, item.y)
    box = mu.fz_bound_glyph(font, item.gid, mu.fz_concat(placed, page_matrix))
    return (box.x0, box.y0, box.x1, box.y1)
