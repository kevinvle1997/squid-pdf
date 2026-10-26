"""A PDF's objects read and written through MuPDF's low-level API, for every part here."""

from __future__ import annotations

import codecs
from collections.abc import Iterator

import pymupdf

from squidpdf.core.pdf.lowlevel.lexing import lexer_of

PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# The keys a hidden copy is kept under, in marked content and in a tag.
HIDDEN_COPY_KEYS = ("ActualText", "Alt", "E")

# The marks a string in UTF-16 starts with.
UTF16_MARKS = (codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)


def pdf_of(doc: pymupdf.Document) -> pymupdf.mupdf.PdfDocument:
    """The same document, as MuPDF's low-level API needs it."""
    return pymupdf.mupdf.pdf_document_from_fz_document(doc.this)


def root_of(doc: pymupdf.Document) -> pymupdf.mupdf.PdfObj:
    """The document's catalog: the dictionary that names its pages, fields and metadata."""
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(mu.pdf_trailer(pdf_of(doc)), mu.PDF_ENUM_NAME_Root)


def values_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each value in a PDF dictionary; none when it isn't one."""
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_val(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def resources_of(holder: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    """The resources a drawing or a Type3 font names things from; null if none."""
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(holder, mu.PDF_ENUM_NAME_Resources)


def items_of(array: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each item in a PDF array."""
    mu = pymupdf.mupdf
    return [mu.pdf_array_get(array, place) for place in range(mu.pdf_array_len(array))]


def bytes_of(stream: pymupdf.mupdf.PdfObj) -> bytes:
    """A stream's bytes, decoded."""
    mu = pymupdf.mupdf
    # A copy: a changed stream lends its own buffer; taking its bytes would empty the stream.
    return mu.fz_buffer_extract_copy(mu.pdf_load_stream(stream))


def as_written(value: pymupdf.mupdf.PdfObj) -> bytes:
    """A dictionary or a string as MuPDF writes it into a drawing: closed up, in ASCII."""
    mu = pymupdf.mupdf
    written = mu.fz_new_buffer(0)  # grows as it's written
    output = mu.FzOutput(written)
    mu.pdf_print_obj(output, value, 1, 1)  # closed up, and ASCII
    mu.fz_close_output(output)
    return mu.fz_buffer_extract(written)


def text_of(string: pymupdf.mupdf.PdfObj) -> str:
    """A string's text, all of it, each NUL in it read as a space.

    MuPDF's own reading stops at a NUL, except in UTF-16; a space keeps the words apart.
    """
    mu = pymupdf.mupdf
    # A string that's an object of the file would be written as its reference.
    string = mu.pdf_resolve_indirect(string)
    written = as_written(string)
    # Written as text, it holds no NUL: MuPDF writes an unprintable string as hex.
    if not written.startswith(b"<"):
        return mu.pdf_to_text_string(string)
    held = bytes.fromhex(written[1:-1].decode())
    # UTF-16, which MuPDF reads past a NUL.
    if held[:2] in UTF16_MARKS:
        return mu.pdf_to_text_string(string).replace("\0", " ")
    # Any other stops at one: read again, with a space for each.
    return mu.pdf_to_text_string(string_of(held.replace(b"\0", b" ")))


def string_of(held: bytes) -> pymupdf.mupdf.PdfObj:
    """A PDF string holding exactly these bytes."""
    mu = pymupdf.mupdf
    # Parsed from hex: MuPDF's own maker of a string takes text, not bytes.
    reader, lexbuf = lexer_of(b"<" + held.hex().encode() + b">")
    # A string belongs to no file, so it's read without one.
    return mu.pdf_parse_stm_obj(mu.PdfDocument(), reader, lexbuf)


def put_text(holder: pymupdf.mupdf.PdfObj, key: str, text: str) -> None:
    """Put `text` in a dictionary under `key`: words left stay, and with none the key goes."""
    mu = pymupdf.mupdf
    # Words of its own left: they stay.
    if text:
        mu.pdf_dict_puts(holder, key, mu.pdf_new_text_string(text))
        return
    # Nothing left: the key goes.
    mu.pdf_dict_dels(holder, key)


def keys_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each key in a PDF dictionary, as a name; none when it isn't one."""
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_key(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def each_reached(
    start: pymupdf.mupdf.PdfObj, keys: tuple[str, ...]
) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each dictionary reached from `start`, following `keys` and lists from each, once.

    Once: a file's tree can lead back to where it was.
    """
    mu = pymupdf.mupdf
    waiting = [start]
    seen: set[int] = set()  # objects of their own reached already, by number
    while waiting:
        node = waiting.pop()
        number = mu.pdf_to_num(node)
        # An object of its own, reached already.
        if mu.pdf_is_indirect(node) and number in seen:
            continue
        seen.add(number)
        # A list: each of its items is reached.
        if mu.pdf_is_array(node):
            waiting += reversed(items_of(node))
            continue
        # Anything else that isn't a dictionary (nothing there, a number) leads nowhere.
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += reversed([mu.pdf_dict_gets(node, key) for key in keys])
