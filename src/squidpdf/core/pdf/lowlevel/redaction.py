from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import (
    bytes_of,
    each_reached,
    items_of,
    pdf_of,
    text_of,
    values_of,
)
from squidpdf.core.pdf.lowlevel.pages import (
    to_pdf_matrix,
    unturned,
)
from squidpdf.core.types import Rect


@dataclass(frozen=True, slots=True, eq=False)
class PageRedaction:
    doc: pymupdf.Document

    def redact(self, page: int, boxes: list[Rect]) -> None:
        """A mark the file holds stays unapplied: applying it isn't this edit's call."""
        mu = pymupdf.mupdf
        # MuPDF applies every mark on the page, so only ours may be there.
        with self._file_marks_aside(page):
            with unturned(self.doc, page) as pdf_page:
                # MuPDF's own calls, as PyMuPDF's read every mark on the page again per box.
                for box in boxes:
                    mark = mu.pdf_create_annot(pdf_page, mu.PDF_ANNOT_REDACT)
                    mu.pdf_set_annot_rect(mark, mu.FzRect(box.x0, box.y0, box.x1, box.y1))
            options = mu.PdfRedactOptions()
            options.black_boxes = 0
            options.text = mu.PDF_REDACT_TEXT_REMOVE
            options.image_method = mu.PDF_REDACT_IMAGE_NONE
            options.line_art = mu.PDF_REDACT_LINE_ART_NONE
            mu.pdf_redact_page(pdf_of(self.doc), pdf_page, options)

    @contextmanager
    def _file_marks_aside(self, page: int) -> Iterator[None]:
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        marks = [
            place
            for place, annotation in enumerate(items_of(listed))
            if mu.pdf_name_eq(
                mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype), mu.PDF_ENUM_NAME_Redact
            )
        ]
        if not marks:
            yield
            return
        before = mu.pdf_copy_array(listed)
        # From the end, so each deletion leaves the places still to read where they were.
        for place in reversed(marks):
            mu.pdf_array_delete(listed, place)
        _sync_annotations(self.doc, page)
        try:
            yield
        finally:
            listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
            _put_back(listed, before, mu.PDF_ENUM_NAME_Redact)
            _sync_annotations(self.doc, page)

    @contextmanager
    def links_kept(self, page: int) -> Iterator[None]:
        """The same object, so a link keeps all it does, even what PyMuPDF can't describe."""
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        if not mu.pdf_is_array(listed):
            yield
            return
        before = mu.pdf_copy_array(listed)
        try:
            yield
        finally:
            listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
            _put_back(listed, before, mu.PDF_ENUM_NAME_Link)
            _sync_links(self.doc, page)

    def drop_links(
        self, page: int, boxes: list[Rect], *, holding: Callable[[str], bool] | None = None
    ) -> None:
        """From Annots: PyMuPDF's links leave out what MuPDF can't follow, as a script."""
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        boxes_shown = [pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) for box in boxes]
        for link in _links_on(self.doc, page):
            over = any(link.area.intersects(box) for box in boxes_shown)
            held = holding is None or any(map(holding, _texts_of_link(link.annotation)))
            if over and held:
                mu.pdf_array_delete(listed, link.place)
        _sync_links(self.doc, page)

    def link_boxes(self, page: int) -> list[Rect]:
        areas = [link.area for link in _links_on(self.doc, page)]
        return [Rect(area.x0, area.y0, area.x1, area.y1) for area in areas]


@dataclass(frozen=True, slots=True, eq=False)
class _Link:
    place: int
    annotation: pymupdf.mupdf.PdfObj
    area: pymupdf.Rect


def _links_on(doc: pymupdf.Document, page: int) -> Iterator[_Link]:
    mu = pymupdf.mupdf
    page_obj = mu.pdf_lookup_page_obj(pdf_of(doc), page)
    listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
    to_page = ~to_pdf_matrix(doc, page)
    for place in reversed(range(mu.pdf_array_len(listed))):
        annotation = mu.pdf_array_get(listed, place)
        subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
        if not mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Link):
            continue
        rect = mu.pdf_to_rect(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Rect))
        yield _Link(
            place, annotation, pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * to_page
        )


def _texts_of_link(annotation: pymupdf.mupdf.PdfObj) -> list[str]:
    mu = pymupdf.mupdf
    texts: list[str] = []
    for action in each_reached(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_A), ("Next",)):
        for value in values_of(action):
            held = values_of(value) if mu.pdf_is_dict(value) else [value]
            texts += [
                _text_held(item)
                for item in held
                if mu.pdf_is_string(item) or mu.pdf_is_stream(item)
            ]
    return texts


def _text_held(item: pymupdf.mupdf.PdfObj) -> str:
    if pymupdf.mupdf.pdf_is_string(item):
        return text_of(item)
    return bytes_of(item).decode("latin-1")


def _put_back(
    listed: pymupdf.mupdf.PdfObj, before: pymupdf.mupdf.PdfObj, kind: pymupdf.mupdf.PdfObj
) -> None:
    """Only deletions happened since `before`, so what's left keeps its order."""
    mu = pymupdf.mupdf
    place = 0
    for annotation in items_of(before):
        if mu.pdf_array_find(listed, annotation) >= 0:
            place += 1
            continue
        subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
        if not mu.pdf_name_eq(subtype, kind):
            continue
        mu.pdf_array_insert(listed, annotation, place)
        place += 1


def _sync_links(doc: pymupdf.Document, page: int) -> None:
    """Have MuPDF read the page's links again: it keeps a list of its own, read once."""
    pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(doc[page].this)
    pymupdf.mupdf.pdf_sync_links(pdf_page)


def _sync_annotations(doc: pymupdf.Document, page: int) -> None:
    pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(doc[page].this)
    pymupdf.mupdf.pdf_sync_annots(pdf_page)
