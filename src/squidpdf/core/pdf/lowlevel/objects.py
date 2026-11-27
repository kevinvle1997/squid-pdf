from __future__ import annotations

import codecs
from collections.abc import Iterator

import pymupdf

from squidpdf.core.pdf.lowlevel.lexing import lexer_of

PDF_NULL = "null"
PDF_REFERENCE = "xref"

HIDDEN_COPY_KEYS = ("ActualText", "Alt", "E")

UTF16_MARKS = (codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)


def pdf_of(doc: pymupdf.Document) -> pymupdf.mupdf.PdfDocument:
    return pymupdf.mupdf.pdf_document_from_fz_document(doc.this)


def object_number(reference: str) -> int:
    return int(reference.split()[0])


def root_of(doc: pymupdf.Document) -> pymupdf.mupdf.PdfObj:
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(mu.pdf_trailer(pdf_of(doc)), mu.PDF_ENUM_NAME_Root)


def values_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_val(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def resources_of(holder: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(holder, mu.PDF_ENUM_NAME_Resources)


def items_of(array: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    mu = pymupdf.mupdf
    return [mu.pdf_array_get(array, place) for place in range(mu.pdf_array_len(array))]


def bytes_of(stream: pymupdf.mupdf.PdfObj) -> bytes:
    mu = pymupdf.mupdf
    # A copy: a changed stream lends its own buffer; taking its bytes would empty the stream.
    return mu.fz_buffer_extract_copy(mu.pdf_load_stream(stream))


def as_written(value: pymupdf.mupdf.PdfObj) -> bytes:
    mu = pymupdf.mupdf
    written = mu.fz_new_buffer(0)
    output = mu.FzOutput(written)
    mu.pdf_print_obj(output, value, 1, 1)  # closed up, and ASCII
    mu.fz_close_output(output)
    return mu.fz_buffer_extract(written)


def text_of(string: pymupdf.mupdf.PdfObj) -> str:
    """Each NUL read as a space: MuPDF's own reading stops at one, except in UTF-16."""
    mu = pymupdf.mupdf
    string = mu.pdf_resolve_indirect(string)
    written = as_written(string)
    if not written.startswith(b"<"):
        return mu.pdf_to_text_string(string)
    held = bytes.fromhex(written[1:-1].decode())
    if held[:2] in UTF16_MARKS:
        return mu.pdf_to_text_string(string).replace("\0", " ")
    return mu.pdf_to_text_string(string_of(held.replace(b"\0", b" ")))


def string_of(held: bytes) -> pymupdf.mupdf.PdfObj:
    mu = pymupdf.mupdf
    # Parsed from hex: MuPDF's own maker of a string takes text, not bytes.
    reader, lexbuf = lexer_of(b"<" + held.hex().encode() + b">")
    return mu.pdf_parse_stm_obj(mu.PdfDocument(), reader, lexbuf)


def metadata_stream(doc: pymupdf.Document) -> pymupdf.mupdf.PdfObj | None:
    mu = pymupdf.mupdf
    stream = mu.pdf_dict_get(root_of(doc), mu.PDF_ENUM_NAME_Metadata)
    return stream if mu.pdf_is_stream(stream) else None


def put_stream(doc: pymupdf.Document, stream: pymupdf.mupdf.PdfObj, written: bytes) -> None:
    mu = pymupdf.mupdf
    mu.pdf_update_stream(pdf_of(doc), stream, mu.fz_new_buffer_from_copied_data(written), 0)


def put_text(holder: pymupdf.mupdf.PdfObj, key: str, text: str) -> None:
    mu = pymupdf.mupdf
    if text:
        mu.pdf_dict_puts(holder, key, mu.pdf_new_text_string(text))
        return
    mu.pdf_dict_dels(holder, key)


def keys_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_key(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def each_reached(
    start: pymupdf.mupdf.PdfObj, keys: tuple[str, ...]
) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Once each: a file's tree can lead back to where it was."""
    mu = pymupdf.mupdf
    waiting = [start]
    seen: set[int] = set()
    while waiting:
        node = waiting.pop()
        number = mu.pdf_to_num(node)
        if mu.pdf_is_indirect(node) and number in seen:
            continue
        seen.add(number)
        if mu.pdf_is_array(node):
            waiting += reversed(items_of(node))
            continue
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += reversed([mu.pdf_dict_gets(node, key) for key in keys])
