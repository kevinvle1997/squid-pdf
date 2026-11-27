from __future__ import annotations

from collections.abc import Iterator

import pymupdf


def lexer_of(written: bytes) -> tuple[pymupdf.mupdf.FzStream, pymupdf.mupdf.PdfLexbuf]:
    mu = pymupdf.mupdf
    reader = mu.fz_open_buffer(mu.fz_new_buffer_from_copied_data(written))
    # Small: the wrapper holds no more, so large overruns it; it grows for a long string.
    return reader, mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)


def tokens_in(
    reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
) -> Iterator[tuple[int, int]]:
    mu = pymupdf.mupdf
    while True:
        start = mu.fz_tell(reader)
        token = mu.pdf_lex(reader, lexbuf)
        if token == mu.PDF_TOK_EOF:
            return
        yield start, token


def is_image_start(
    token: int, *, drawing: bytes, start: int, reader: pymupdf.mupdf.FzStream
) -> bool:
    is_operator = token == pymupdf.mupdf.PDF_TOK_KEYWORD
    return is_operator and _operator_at(drawing, start, reader) == b"BI"


def _operator_at(drawing: bytes, start: int, reader: pymupdf.mupdf.FzStream) -> bytes:
    return drawing[start : pymupdf.mupdf.fz_tell(reader)].split()[-1]


def read_past_dictionary(
    reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
) -> None:
    """Counts only << and >>, so a >> in an array closes it, though not for MuPDF."""
    mu = pymupdf.mupdf
    depth = 1
    while depth:
        token = mu.pdf_lex(reader, lexbuf)
        if token == mu.PDF_TOK_EOF:
            return
        if token == mu.PDF_TOK_OPEN_DICT:
            depth += 1
        if token == mu.PDF_TOK_CLOSE_DICT:
            depth -= 1


def load_inline_image(
    pdf: pymupdf.mupdf.PdfDocument,
    dictionary: pymupdf.mupdf.PdfObj,
    *,
    reader: pymupdf.mupdf.FzStream,
    resources: pymupdf.mupdf.PdfObj,
) -> pymupdf.mupdf.FzImage:
    mu = pymupdf.mupdf
    stack = mu.pdf_resource_stack()  # held here: the wrapper below only points at it
    stack.resources = resources.m_internal
    stack.next = None
    return mu.pdf_load_inline_image(pdf, mu.PdfResourceStack(stack), dictionary, reader)
