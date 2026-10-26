"""A drawing's bytes, read a token at a time with MuPDF's lexer."""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf


def lexer_of(written: bytes) -> tuple[pymupdf.mupdf.FzStream, pymupdf.mupdf.PdfLexbuf]:
    """A reader over `written`, and the buffer MuPDF's lexer reads each token into."""
    mu = pymupdf.mupdf
    reader = mu.fz_open_buffer(mu.fz_new_buffer_from_copied_data(written))
    # Small: the wrapper holds no more, so large overruns it; it grows for a long string.
    return reader, mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)


def tokens_in(
    reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
) -> Iterator[tuple[int, int]]:
    """Where each token `reader` reads starts, and the token, to the end.

    A caller may read on, or back, before asking for the next: it starts where it's left.
    """
    mu = pymupdf.mupdf
    while True:
        start = mu.fz_tell(reader)
        token = mu.pdf_lex(reader, lexbuf)
        # The end of what's written.
        if token == mu.PDF_TOK_EOF:
            return
        yield start, token


def is_image_start(
    token: int, *, drawing: bytes, start: int, reader: pymupdf.mupdf.FzStream
) -> bool:
    """Whether the token just read from `drawing` is BI, which starts an image written in it."""
    is_operator = token == pymupdf.mupdf.PDF_TOK_KEYWORD
    return is_operator and _operator_at(drawing, start, reader) == b"BI"


def _operator_at(drawing: bytes, start: int, reader: pymupdf.mupdf.FzStream) -> bytes:
    """The operator just read from `drawing`: the last word read, past any space or comment."""
    return drawing[start : pymupdf.mupdf.fz_tell(reader)].split()[-1]


def read_past_dictionary(
    reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
) -> None:
    """Read on past the >> that closes the dictionary `reader` is in, or to the end if none.

    Counts only << and >>, so a >> in an array closes it, though not for MuPDF.
    """
    mu = pymupdf.mupdf
    depth = 1  # dictionaries open: the one `reader` is in, and each in it
    while depth:
        token = mu.pdf_lex(reader, lexbuf)
        # The end of the drawing: it never ends.
        if token == mu.PDF_TOK_EOF:
            return
        # A dictionary in it.
        if token == mu.PDF_TOK_OPEN_DICT:
            depth += 1
        # The end of one.
        if token == mu.PDF_TOK_CLOSE_DICT:
            depth -= 1


def load_inline_image(
    pdf: pymupdf.mupdf.PdfDocument,
    dictionary: pymupdf.mupdf.PdfObj,
    *,
    reader: pymupdf.mupdf.FzStream,
    resources: pymupdf.mupdf.PdfObj,
) -> pymupdf.mupdf.FzImage:
    """Read an image's bytes from `reader`, as MuPDF does drawing it: the image."""
    mu = pymupdf.mupdf
    stack = mu.pdf_resource_stack()  # held here: the wrapper below only points at it
    stack.resources = resources.m_internal
    stack.next = None
    return mu.pdf_load_inline_image(pdf, mu.PdfResourceStack(stack), dictionary, reader)
