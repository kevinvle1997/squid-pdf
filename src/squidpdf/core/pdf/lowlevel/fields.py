"""A file's annotations and form fields, walked as MuPDF's low-level API holds them."""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import each_reached, items_of, pdf_of

_FIELD_KIDS = ("Kids",)  # what leads on from a form field: the fields and parts under it

FIELD_VALUE = "V"  # a form field's value


def form_fields(
    doc: pymupdf.Document, root: pymupdf.mupdf.PdfObj
) -> list[pymupdf.mupdf.PdfObj]:
    """Each form field once: those the form lists, then those a page shows that it doesn't.

    A broken or half-flattened form can leave off a field that still shows on the page.
    """
    mu = pymupdf.mupdf
    form = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_AcroForm)
    fields = list(each_reached(mu.pdf_dict_get(form, mu.PDF_ENUM_NAME_Fields), _FIELD_KIDS))
    # By number: one written inline has none of its own (0), and is a field of its own.
    reached = {mu.pdf_to_num(field) for field in fields if mu.pdf_is_indirect(field)}
    for _page, annotation in each_annotation(doc):
        # A comment or a link, not a form field's part.
        if not is_widget(annotation):
            continue
        for field in field_line(annotation):
            # An object of its own, reached already, from the form's list or another part.
            if mu.pdf_is_indirect(field) and mu.pdf_to_num(field) in reached:
                continue
            reached.add(mu.pdf_to_num(field))
            fields.append(field)
    return fields


def each_annotation(doc: pymupdf.Document) -> Iterator[tuple[int, pymupdf.mupdf.PdfObj]]:
    """Each annotation on every page, with its page: comments, links, form fields' parts."""
    mu = pymupdf.mupdf
    pdf = pdf_of(doc)
    for page in range(mu.pdf_count_pages(pdf)):
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        for annotation in items_of(mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)):
            yield page, annotation


def is_widget(annotation: pymupdf.mupdf.PdfObj) -> bool:
    """Whether an annotation is a form field's part on the page (a widget)."""
    mu = pymupdf.mupdf
    subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
    return bool(mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Widget))


def field_line(annotation: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """An annotation, and the fields above it through each one's Parent, from it up."""
    mu = pymupdf.mupdf
    line: list[pymupdf.mupdf.PdfObj] = []
    numbers: set[int] = set()  # those in the line, by their numbers
    node = annotation
    # Up to the top, once each: a broken file's parents can go round.
    while mu.pdf_is_dict(node) and mu.pdf_to_num(node) not in numbers:
        line.append(node)
        numbers.add(mu.pdf_to_num(node))
        node = mu.pdf_dict_get(node, mu.PDF_ENUM_NAME_Parent)
    return line
