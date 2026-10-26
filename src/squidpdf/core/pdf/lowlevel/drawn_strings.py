"""The strings a drawing writes, read as MuPDF reads them and where another reader may."""

from __future__ import annotations

import codecs
import re
from collections.abc import Callable
from dataclasses import dataclass

import pymupdf

from squidpdf.core.constants import IMAGE_SOONER_BY
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

# The EI that ends an image written into a drawing, as MuPDF finds it past the bytes.
_INLINE_IMAGE_END = re.compile(rb"EI(?=[\x00-\x20</]|\Z)")

# An EI a reader that doesn't count an image's bytes ends it at: a space, then plain bytes.
_EI_BEFORE_DRAWING = re.compile(
    rb"EI[\t\n\r ](?=[\x00\t\n\r\x20-\x7f]{10}|[\x00\t\n\r\x20-\x7f]*\Z)"
)

# An inline image's filters, under either key, in the order each reader looks: MuPDF and
# Poppler read /Filter first, pdf.js /F.
_FILTER_KEYS = (("Filter", "F"), ("F", "Filter"))

_BITS_IN_A_BYTE = 8

# The encoding a string reads in, by the mark it starts with.
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
    """Each string an annotation's drawing writes, and the drawings it draws, as text.

    Read as written, not as drawn: another viewer may draw what MuPDF won't.
    """
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
    """Each string `drawing` writes, as text, and in an image where another reader ends it.

    After an image MuPDF can't read, every string to the end: nothing says where its
    bytes end. `resources` are those it names an image's colour space from.
    """
    reader, lexbuf = lexer_of(drawing)
    found: list[str] = []
    for start, token in tokens_in(reader, lexbuf):
        # A string: read again from its start, as an object, for its text.
        if token == pymupdf.mupdf.PDF_TOK_STRING:
            found.append(_string_from(pdf, start, reader=reader, lexbuf=lexbuf))
            continue
        if not is_image_start(token, drawing=drawing, start=start, reader=reader):
            continue
        image = read_past_image(pdf, reader, lexbuf, drawing=drawing, resources=resources)
        # An image MuPDF can read: it reads on after it, and another reader may in it.
        if image is not None:
            found += strings_past_an_end_in(image)
            continue
        # One it can't: what's left may hold anything.
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
    """Read past an image written into `drawing`, as MuPDF does: the image, or None if not.

    `reader` is just past its BI.
    """
    mu = pymupdf.mupdf
    # MuPDF raises on an image it can't read: its dictionary, colour space or bytes.
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
    # No EI after its bytes: MuPDF reads no further.
    if end is None:
        return None
    mu.fz_seek(reader, end.end(), 0)
    written = drawing[image_from : end.start()]
    readings = [_filters_of(dictionary, keys) for keys in _FILTER_KEYS]
    # Each row's bytes, rounded up; a predictor's byte a row is left out, the safe side.
    row = -(-loaded.w() * loaded.n() * loaded.bpc() // _BITS_IN_A_BYTE)
    # The fewest any reading gives, and an end of its own only if every reading marks one.
    counted = min(fewest_giving(written, filters, row * loaded.h()) for filters in readings)
    return InlineImage(
        written,
        counted=max(counted - IMAGE_SOONER_BY, 0),
        marks_its_end=all(marks_its_end(filters) for filters in readings),
    )


def strings_in(pdf: pymupdf.mupdf.PdfDocument, written: bytes) -> list[str]:
    """Each string in `written`, part of a drawing, as text."""
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
    """The text of the string `reader` just read, read again from `start` as an object."""
    mu = pymupdf.mupdf
    mu.fz_seek(reader, start, 0)
    return text_of(mu.pdf_parse_stm_obj(pdf, reader, lexbuf))


def _filters_of(dictionary: pymupdf.mupdf.PdfObj, keys: tuple[str, str]) -> list[str]:
    """An image's filters under the first of `keys` it has, by name, in the order undone."""
    mu = pymupdf.mupdf
    written = (mu.pdf_dict_gets(dictionary, key) for key in keys)
    # Null: neither key is there.
    image_filter = next((value for value in written if not mu.pdf_is_null(value)), None)
    if image_filter is None:
        return []
    filters = items_of(image_filter) if mu.pdf_is_array(image_filter) else [image_filter]
    # Anything but a name reads as "", a filter no reader sizes.
    return [mu.pdf_to_name(item) for item in filters]


def strings_anywhere_in(written: bytes) -> list[str]:
    """Each string in `written`, part of a drawing, as text, wherever a reader may start one.

    That's at any ( or <, since a reader may read the bytes before it otherwise than MuPDF.
    """
    held = literal_strings_in(written) + hex_strings_in(written)
    return [text for string in held for text in _texts_held_in(string)]


def strings_past_an_end_in(image: InlineImage) -> list[str]:
    """Each string in an image's bytes past where another reader may end it, wherever it starts.

    That reader reads the rest of its bytes as drawing. Only such an end counts, so bytes
    that merely hold an EI, as compressed ones may, aren't read.
    """
    ends: list[int] = []
    before_drawing = _EI_BEFORE_DRAWING.search(image.written)
    # A reader that doesn't count them ends it at an EI followed by what reads as drawing.
    if before_drawing is not None and not image.marks_its_end:
        ends.append(before_drawing.end())
    past_count = image.written.find(b"EI", image.counted)
    # One that counts them ends it at the first EI after, whatever follows.
    if past_count != -1:
        ends.append(past_count + len(b"EI"))
    # Neither: every reader ends it where MuPDF does.
    if not ends:
        return []
    return strings_anywhere_in(image.written[min(ends) :])


def _texts_held_in(held: bytes) -> list[str]:
    """The text of a string holding `held`, plainly too, and in each encoding a mark names.

    A string may start at a mark inside it, so `held` is read from either byte a UTF-16
    letter may start on, whole and cut at each ), where such a string ends.
    """
    texts = [text_of(string_of(held))]
    # A UTF-16 mark makes it read only as UTF-16; a space before the mark reads it plain.
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
    """An image written into a drawing, which MuPDF reads past."""

    written: bytes  # its bytes, from past its ID to the EI MuPDF ends it at
    counted: int  # the fewest of them a reader that counts its bytes may take as the image
    # Whether its filter ends it, so a reader that doesn't count looks for that.
    marks_its_end: bool
