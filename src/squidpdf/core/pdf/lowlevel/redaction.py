"""Redaction on a page: its letters deleted, with the links and comments on them."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import items_of, pdf_of
from squidpdf.core.pdf.lowlevel.pages import (
    to_pdf_matrix,
    unturned,
)
from squidpdf.core.types import Rect


@dataclass(frozen=True, slots=True, eq=False)
class PageRedaction:
    """What a redaction deletes from a page: its letters, and the links and comments on them."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def redact(self, page: int, boxes: list[Rect]) -> None:
        """Delete every letter whose box touches one of `boxes`, and any link or comment on it.

        Only a comment that writes its words on the page (a FreeText) goes. A redaction
        mark the file already holds stays unapplied: applying it is not this edit's call.
        """
        mu = pymupdf.mupdf
        # MuPDF applies every mark on the page, so only ours may be there.
        with self._file_marks_aside(page):
            # Unturned: a box is read unturned, and MuPDF places a mark as it's shown.
            with unturned(self.doc, page) as pdf_page:
                # MuPDF's own calls, as PyMuPDF's read every mark on the page again per box.
                for box in boxes:
                    mark = mu.pdf_create_annot(pdf_page, mu.PDF_ANNOT_REDACT)
                    mu.pdf_set_annot_rect(mark, mu.FzRect(box.x0, box.y0, box.x1, box.y1))
            options = mu.PdfRedactOptions()
            options.black_boxes = 0  # nothing drawn where the letters were
            options.text = mu.PDF_REDACT_TEXT_REMOVE
            options.image_method = mu.PDF_REDACT_IMAGE_NONE
            options.line_art = mu.PDF_REDACT_LINE_ART_NONE
            mu.pdf_redact_page(pdf_of(self.doc), pdf_page, options)

    @contextmanager
    def _file_marks_aside(self, page: int) -> Iterator[None]:
        """The page's redaction marks, out of its Annots in the `with`, back in place after."""
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
        # No mark of the file's own: nothing to set aside.
        if not marks:
            yield
            return
        # The list as it was, a copy that holds on to each mark while it's out.
        before = mu.pdf_copy_array(listed)
        # From the end, so each deletion leaves the places still to read where they were.
        for place in reversed(marks):
            mu.pdf_array_delete(listed, place)
        _sync_annotations(self.doc, page)
        yield
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        _put_back(listed, before, mu.PDF_ENUM_NAME_Redact)
        _sync_annotations(self.doc, page)

    @contextmanager
    def links_kept(self, page: int) -> Iterator[None]:
        """Each link the page loses inside the `with` comes back, as it was.

        The same object, in its place in the page's list of annotations (its Annots),
        so a link keeps its look and what it does, even what PyMuPDF can't describe.
        Any other annotation the page loses stays gone.
        """
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        # The list as it was, a copy that holds on to each entry while MuPDF drops some.
        before = mu.pdf_copy_array(listed) if mu.pdf_is_array(listed) else None
        yield
        # No list before: nothing to put back.
        if before is None:
            return
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        _put_back(listed, before, mu.PDF_ENUM_NAME_Link)
        _sync_links(self.doc, page)

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`, whatever it does.

        Read from the page's own list of annotations (its Annots): PyMuPDF's list
        of links leaves out what MuPDF can't follow, a script or a form reset.
        """
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        to_page = ~to_pdf_matrix(self.doc, page)
        boxes_shown = [pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) for box in boxes]
        # From the end, so each deletion leaves the places still to read where they were.
        for place in reversed(range(mu.pdf_array_len(listed))):
            annotation = mu.pdf_array_get(listed, place)
            subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
            # Another kind of annotation: a note, a form field.
            if not mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Link):
                continue
            rect = mu.pdf_to_rect(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Rect))
            link_area = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * to_page
            if any(link_area.intersects(box) for box in boxes_shown):
                mu.pdf_array_delete(listed, place)
        _sync_links(self.doc, page)


def _put_back(
    listed: pymupdf.mupdf.PdfObj, before: pymupdf.mupdf.PdfObj, kind: pymupdf.mupdf.PdfObj
) -> None:
    """Put each annotation of `kind` that `before` lists and `listed` lost back in its place.

    Only deletions happened since `before`, so what's left keeps its order.
    """
    mu = pymupdf.mupdf
    place = 0  # where the next one goes back
    for annotation in items_of(before):
        # Still listed: the next one goes after it.
        if mu.pdf_array_find(listed, annotation) >= 0:
            place += 1
            continue
        subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
        # Another kind of annotation: it stays gone.
        if not mu.pdf_name_eq(subtype, kind):
            continue
        mu.pdf_array_insert(listed, annotation, place)
        place += 1


def _sync_links(doc: pymupdf.Document, page: int) -> None:
    """Have MuPDF read the page's links again: it keeps a list of its own, read once."""
    pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(doc[page].this)
    pymupdf.mupdf.pdf_sync_links(pdf_page)


def _sync_annotations(doc: pymupdf.Document, page: int) -> None:
    """Have MuPDF read the page's annotations again: it keeps its own list, read once."""
    pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(doc[page].this)
    pymupdf.mupdf.pdf_sync_annots(pdf_page)
