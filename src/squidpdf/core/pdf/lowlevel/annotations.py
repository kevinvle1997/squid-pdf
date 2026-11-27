"""Where the file keeps each annotation its pages list: inside the list, or apart."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.letters_read import LettersRead
from squidpdf.core.pdf.lowlevel.objects import items_of, pdf_of


@dataclass(frozen=True, slots=True, eq=False)
class Annotations:
    """The annotations each page lists (its Annots): comments, links, form fields' parts."""

    doc: pymupdf.Document  # open already; whoever opened it closes it
    letters_read: LettersRead  # forgotten where a write here changes the page

    def each_its_own_object(self) -> None:
        """Move each annotation written inside its page's list into an object of its own.

        To show one with no drawing (no appearance), MuPDF makes one up and keeps it out of
        the file, but inside the list it can't once the page loads again: the made-up
        drawing's number goes in the file, and the next object the file gains takes it.
        """
        self.letters_read.forget()
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        for page in range(mu.pdf_count_pages(pdf)):
            listed = mu.pdf_dict_get(mu.pdf_lookup_page_obj(pdf, page), mu.PDF_ENUM_NAME_Annots)
            for place, annotation in enumerate(items_of(listed)):
                # Apart already, or no annotation at all.
                if mu.pdf_is_indirect(annotation) or not mu.pdf_is_dict(annotation):
                    continue
                mu.pdf_array_put(listed, place, mu.pdf_add_object(pdf, annotation))
