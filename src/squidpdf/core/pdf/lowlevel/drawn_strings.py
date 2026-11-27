from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pymupdf

import squidpdf.core.constants as constants
from squidpdf.core.pdf.filters import fewest_giving, marks_its_end
from squidpdf.core.pdf.lowlevel.drawings import appearance_streams, each_drawing
from squidpdf.core.pdf.lowlevel.lexing import (
    is_image_start,
    lexer_of,
    load_inline_image,
    tokens_in,
)
from squidpdf.core.pdf.lowlevel.objects import (
    UTF16_MARKS,
    bytes_of,
    items_of,
    resources_of,
    string_of,
    text_of,
)
from squidpdf.core.pdf.strings import hex_strings_in, literal_strings_in, may_hold

_INLINE_IMAGE_END = re.compile(rb"EI(?=[\x00-\x20</]|\Z)")

_EI_BEFORE_DRAWING = re.compile(
    rb"EI[\t\n\r ](?=[\x00\t\n\r\x20-\x7f]{10}|[\x00\t\n\r\x20-\x7f]*\Z)"
)

# MuPDF and Poppler read /Filter first, pdf.js /F.
_FILTER_KEYS = (("Filter", "F"), ("F", "Filter"))

_BITS_IN_A_BYTE = 8

_MARKED_CODECS = {
    codecs.BOM_UTF16_BE: "utf-16-be",
    codecs.BOM_UTF16_LE: "utf-16-le",
    codecs.BOM_UTF8: "utf-8",
}


def strings_drawn_by(
    pdf: pymupdf.mupdf.PdfDocument,
    annotation: pymupdf.mupdf.PdfObj,
    holding: Callable[[str], bool],
) -> list[str]:
    """Read as written, not as drawn: another viewer may draw what MuPDF won't."""
    appearances = [
        ([appearance], resources_of(appearance))
        for appearance in appearance_streams(annotation)
    ]
    drawings = [
        (bytes_of(stream), resources)
        for streams, resources in each_drawing(appearances)
        for stream in streams
    ]
    return [
        text
        for drawing, resources in drawings
        if may_hold(drawing, holding)
        for text in _strings_written_in(pdf, drawing, resources)
    ]


def _strings_written_in(
    pdf: pymupdf.mupdf.PdfDocument, drawing: bytes, resources: pymupdf.mupdf.PdfObj
) -> list[str]:
    """After an image MuPDF can't read, every string to the end: nothing says where it ends."""
    reader, lexbuf = lexer_of(drawing)
    found: list[str] = []
    for start, token in tokens_in(reader, lexbuf):
        if token == pymupdf.mupdf.PDF_TOK_STRING:
            found.append(_string_from(pdf, start, reader=reader, lexbuf=lexbuf))
            continue
        if not is_image_start(token, drawing=drawing, start=start, reader=reader):
            continue
        image = read_past_image(pdf, reader, lexbuf, drawing=drawing, resources=resources)
        if image is not None:
            found += strings_past_an_end_in(image)
            continue
        rest = drawing[start:]
        return found + strings_in(pdf, rest) + strings_anywhere_in(rest)
    return found


def read_past_image(
    pdf: pymupdf.mupdf.PdfDocument,
    reader: pymupdf.mupdf.FzStream,
    lexbuf: pymupdf.mupdf.PdfLexbuf,
    *,
    drawing: bytes,
    resources: pymupdf.mupdf.PdfObj,
) -> InlineImage | None:
    mu = pymupdf.mupdf
    try:
        dictionary = mu.pdf_parse_dict(pdf, reader, lexbuf)
        # A carriage return and line feed after ID are one line end.
        after_id = mu.fz_read_byte(reader)
        two_byte_end = after_id == ord("\r") and mu.fz_peek_byte(reader) == ord("\n")
        if two_byte_end:
            mu.fz_read_byte(reader)
        image_from = mu.fz_tell(reader)
        loaded = load_inline_image(pdf, dictionary, reader=reader, resources=resources)
    except mu.FzErrorSyntax, mu.FzErrorFormat:
        return None
    end = _INLINE_IMAGE_END.search(drawing, mu.fz_tell(reader))
    if end is None:
        return None
    mu.fz_seek(reader, end.end(), 0)
    written = drawing[image_from : end.start()]
    readings = [_filters_of(dictionary, keys) for keys in _FILTER_KEYS]
    row = -(-loaded.w() * loaded.n() * loaded.bpc() // _BITS_IN_A_BYTE)
    counted = min(fewest_giving(written, filters, row * loaded.h()) for filters in readings)
    return InlineImage(
        written,
        counted=max(counted - constants.IMAGE_SOONER_BY, 0),
        marks_its_end=all(marks_its_end(filters) for filters in readings),
    )


def strings_in(pdf: pymupdf.mupdf.PdfDocument, written: bytes) -> list[str]:
    reader, lexbuf = lexer_of(written)
    return [
        _string_from(pdf, start, reader=reader, lexbuf=lexbuf)
        for start, token in tokens_in(reader, lexbuf)
        if token == pymupdf.mupdf.PDF_TOK_STRING
    ]


def _string_from(
    pdf: pymupdf.mupdf.PdfDocument,
    start: int,
    *,
    reader: pymupdf.mupdf.FzStream,
    lexbuf: pymupdf.mupdf.PdfLexbuf,
) -> str:
    mu = pymupdf.mupdf
    mu.fz_seek(reader, start, 0)
    return text_of(mu.pdf_parse_stm_obj(pdf, reader, lexbuf))


def _filters_of(dictionary: pymupdf.mupdf.PdfObj, keys: tuple[str, str]) -> list[str]:
    mu = pymupdf.mupdf
    written = (mu.pdf_dict_gets(dictionary, key) for key in keys)
    image_filter = next((value for value in written if not mu.pdf_is_null(value)), None)
    if image_filter is None:
        return []
    filters = items_of(image_filter) if mu.pdf_is_array(image_filter) else [image_filter]
    return [mu.pdf_to_name(item) for item in filters]


def strings_anywhere_in(written: bytes) -> list[str]:
    """At any ( or <: a reader may read the bytes before it otherwise than MuPDF."""
    held = literal_strings_in(written) + hex_strings_in(written)
    return [text for string in held for text in _texts_held_in(string)]


def strings_past_an_end_in(image: InlineImage) -> list[str]:
    ends: list[int] = []
    before_drawing = _EI_BEFORE_DRAWING.search(image.written)
    if before_drawing is not None and not image.marks_its_end:
        ends.append(before_drawing.end())
    past_count = image.written.find(b"EI", image.counted)
    if past_count != -1:
        ends.append(past_count + len(b"EI"))
    if not ends:
        return []
    return strings_anywhere_in(image.written[min(ends) :])


def _texts_held_in(held: bytes) -> list[str]:
    """A string may start at a mark inside it, so `held` is read from either byte too."""
    texts = [text_of(string_of(held))]
    if held[:2] in UTF16_MARKS:
        texts.append(text_of(string_of(b" " + held)))
    for mark, codec in _MARKED_CODECS.items():
        if mark not in held:
            continue
        pieces = [held, *held.split(b")")]
        texts += [
            piece[skip:].decode(codec, "replace").replace("\0", " ")
            for piece in pieces
            for skip in (0, 1)
        ]
    return texts


@dataclass(frozen=True, slots=True)
class InlineImage:
    written: bytes = field(repr=False)
    counted: int  # the fewest of them a reader that counts its bytes may take as the image
    marks_its_end: bool
