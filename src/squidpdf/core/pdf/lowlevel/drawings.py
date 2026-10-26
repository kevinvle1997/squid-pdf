"""Each drawing a page or an annotation keeps in a stream of its own."""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import items_of, resources_of, values_of

# A drawing's stream, and the resources it names things from.
type _Drawing = tuple[pymupdf.mupdf.PdfObj, pymupdf.mupdf.PdfObj]


def appearances_of(page: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each appearance stream of each annotation on the page: plain, hovered and pressed."""
    mu = pymupdf.mupdf
    for annotation in items_of(mu.pdf_dict_get(page, mu.PDF_ENUM_NAME_Annots)):
        yield from appearance_streams(annotation)


def appearance_streams(annotation: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each appearance of an annotation: the drawing it shows, in each state."""
    mu = pymupdf.mupdf
    for appearance in values_of(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_AP)):
        # One stream, or one for each state, as a checkbox's on and off.
        yield from [appearance] if mu.pdf_is_stream(appearance) else values_of(appearance)


def each_drawing(
    waiting: list[tuple[list[pymupdf.mupdf.PdfObj], pymupdf.mupdf.PdfObj]],
) -> Iterator[tuple[list[pymupdf.mupdf.PdfObj], pymupdf.mupdf.PdfObj]]:
    """Each drawing waiting, with its resources, then each drawing they draw, once each."""
    mu = pymupdf.mupdf
    # Drawings already waiting, by object number: a form can draw itself.
    seen: set[int] = set()
    while waiting:
        streams, resources = waiting.pop()
        yield [stream for stream in streams if mu.pdf_is_stream(stream)], resources
        for drawn, own_resources in _drawn_by(resources):
            # Already waiting: nothing more to read.
            if mu.pdf_to_num(drawn) in seen:
                continue
            seen.add(mu.pdf_to_num(drawn))
            # With none of its own, it names its user's resources, read already.
            waiting.append(([drawn], own_resources))


def _drawn_by(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each drawing `resources` names, each kept in a stream of its own."""
    yield from _forms_in(resources)
    yield from _tiling_patterns_in(resources)
    yield from _soft_masks_in(resources)
    yield from _type3_letters_in(resources)


def _forms_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each form (XObject) `resources` names."""
    mu = pymupdf.mupdf
    for xobject in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_XObject)):
        subtype = mu.pdf_dict_get(xobject, mu.PDF_ENUM_NAME_Subtype)
        # A form: an image holds no drawing.
        if mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Form):
            yield xobject, resources_of(xobject)


def _tiling_patterns_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each tiling pattern `resources` names."""
    mu = pymupdf.mupdf
    for pattern in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Pattern)):
        # A tiling pattern: a shading one is a plain dictionary.
        if mu.pdf_is_stream(pattern):
            yield pattern, resources_of(pattern)


def _soft_masks_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """The drawing of each soft mask the graphics states in `resources` name."""
    mu = pymupdf.mupdf
    for state in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_ExtGState)):
        mask = mu.pdf_dict_get(state, mu.PDF_ENUM_NAME_SMask)
        group = mu.pdf_dict_get(mask, mu.PDF_ENUM_NAME_G)
        # A soft mask, not absent or /None.
        if mu.pdf_is_stream(group):
            yield group, resources_of(group)


def _type3_letters_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each letter of each Type3 font `resources` names, with its font's resources."""
    mu = pymupdf.mupdf
    for font in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Font)):
        subtype = mu.pdf_dict_get(font, mu.PDF_ENUM_NAME_Subtype)
        # A Type3 font, whose letters are drawings.
        if mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Type3):
            letters = mu.pdf_dict_get(font, mu.PDF_ENUM_NAME_CharProcs)
            yield from ((letter, resources_of(font)) for letter in values_of(letters))
