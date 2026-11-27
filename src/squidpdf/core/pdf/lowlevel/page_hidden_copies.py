from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.drawings import appearances_of, each_drawing
from squidpdf.core.pdf.lowlevel.drawn_strings import (
    InlineImage,
    read_past_image,
    strings_anywhere_in,
    strings_in,
    strings_past_an_end_in,
)
from squidpdf.core.pdf.lowlevel.lexing import (
    is_image_start,
    lexer_of,
    read_past_dictionary,
    tokens_in,
)
from squidpdf.core.pdf.lowlevel.objects import (
    HIDDEN_COPY_KEYS,
    as_written,
    bytes_of,
    items_of,
    pdf_of,
    put_stream,
    put_text,
    resources_of,
    text_of,
    values_of,
)
from squidpdf.core.pdf.strings import (
    may_hold_a_string,
)


@dataclass(frozen=True, slots=True, eq=False)
class PageHiddenCopies:
    doc: pymupdf.Document

    def read(self, page: int) -> list[str]:
        mu = pymupdf.mupdf
        found: list[str] = []
        for streams, resources in self._drawings(page):
            for marking in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)):
                found += _hidden_copies_in(marking)
            for stream in streams:
                found += self._hidden_copies_written_in(bytes_of(stream), resources)
        return found

    def rewrite(self, page: int, rewritten: Callable[[str], str]) -> None:
        mu = pymupdf.mupdf
        for streams, resources in self._drawings(page):
            properties = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)
            for marking in values_of(properties):
                _rewrite_hidden_copies_in(marking, rewritten)
            for stream in streams:
                self._rewrite_written(stream, resources, rewritten)

    def _drawings(
        self, page: int
    ) -> Iterator[tuple[list[pymupdf.mupdf.PdfObj], pymupdf.mupdf.PdfObj]]:
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        streams = items_of(contents) if mu.pdf_is_array(contents) else [contents]
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        waiting = [(streams, resources)] + [
            ([appearance], resources_of(appearance)) for appearance in appearances_of(page_obj)
        ]
        yield from each_drawing(waiting)

    def _rewrite_written(
        self,
        stream: pymupdf.mupdf.PdfObj,
        resources: pymupdf.mupdf.PdfObj,
        rewritten: Callable[[str], str],
    ) -> None:
        drawing = bytes_of(stream)
        pieces: list[bytes] = []
        kept_to = 0
        for start, end, marking in self._markings_in(drawing, resources):
            # Not read whole, or an image: no reading is every reader's, so it stays.
            if not isinstance(marking, pymupdf.mupdf.PdfObj):
                continue
            lost_string = self._loses_a_string(marking, written=drawing[start:end])
            changed = _rewrite_hidden_copies_in(marking, rewritten)
            # Written as MuPDF reads it, so no reader finds the other reading.
            if changed or lost_string:
                pieces += [drawing[kept_to:start], as_written(marking)]
                kept_to = end
        if not pieces:
            return
        new_drawing = b"".join([*pieces, drawing[kept_to:]])
        put_stream(self.doc, stream, new_drawing)

    def _hidden_copies_written_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> list[str]:
        """After a dictionary not read whole or an unread image, every string to the end."""
        found: list[str] = []
        unread_from = len(drawing)
        for start, end, marking in self._markings_in(drawing, resources):
            written = drawing[start:end]
            if marking is None:
                unread_from = min(unread_from, start)
                continue
            if isinstance(marking, InlineImage):
                found += strings_past_an_end_in(marking)
                continue
            if self._loses_a_string(marking, written=written):
                found += strings_in(pdf_of(self.doc), written)
            found += _hidden_copies_in(marking)
        rest = drawing[unread_from:]
        return found + strings_in(pdf_of(self.doc), rest) + strings_anywhere_in(rest)

    def _loses_a_string(self, marking: pymupdf.mupdf.PdfObj, *, written: bytes) -> bool:
        """As with a key written twice: MuPDF keeps the last, another reader the other."""
        if not may_hold_a_string(written):
            return False
        return len(strings_in(pdf_of(self.doc), as_written(marking))) < len(
            strings_in(pdf_of(self.doc), written)
        )

    def _markings_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> Iterator[tuple[int, int, pymupdf.mupdf.PdfObj | InlineImage | None]]:
        """Every dictionary counts, as MuPDF keeps one even before its tag."""
        mu = pymupdf.mupdf
        if b"<<" not in drawing:
            return
        reader, lexbuf = lexer_of(drawing)
        may_hold_image = b"BI" in drawing
        for start, token in tokens_in(reader, lexbuf):
            if token == mu.PDF_TOK_OPEN_DICT:
                dictionary = self._dictionary_in(reader, lexbuf)
                yield start, mu.fz_tell(reader), dictionary
                continue
            starts_image = may_hold_image and is_image_start(
                token, drawing=drawing, start=start, reader=reader
            )
            if not starts_image:
                continue
            image = read_past_image(
                pdf_of(self.doc), reader, lexbuf, drawing=drawing, resources=resources
            )
            if image is not None:
                yield start, mu.fz_tell(reader), image
                continue
            yield start, len(drawing), None
            return

    def _dictionary_in(
        self, reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
    ) -> pymupdf.mupdf.PdfObj | None:
        """Whole: MuPDF's parser and a count of << and >> end at the same >>."""
        mu = pymupdf.mupdf
        opened = mu.fz_tell(reader)
        try:
            dictionary = mu.pdf_parse_dict(pdf_of(self.doc), reader, lexbuf)
        except mu.FzErrorSyntax:
            dictionary = None
        parsed_to = mu.fz_tell(reader)
        mu.fz_seek(reader, opened, 0)
        read_past_dictionary(reader, lexbuf)
        counted_to = mu.fz_tell(reader)
        read_whole = dictionary is not None and counted_to == parsed_to
        if read_whole:
            return dictionary
        mu.fz_seek(reader, max(parsed_to, counted_to), 0)
        return None


def _hidden_copies_in(marking: pymupdf.mupdf.PdfObj) -> list[str]:
    texts = (_text_at(marking, key) for key in HIDDEN_COPY_KEYS)
    return [text for text in texts if text is not None]


def _rewrite_hidden_copies_in(
    marking: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
) -> bool:
    changed = False
    for key in HIDDEN_COPY_KEYS:
        hidden_copy = _text_at(marking, key)
        if hidden_copy is None:
            continue
        kept = rewritten(hidden_copy)
        if kept == hidden_copy:
            continue
        changed = True
        put_text(marking, key, kept)
    return changed


def _text_at(holder: pymupdf.mupdf.PdfObj, key: str) -> str | None:
    mu = pymupdf.mupdf
    value = mu.pdf_dict_gets(holder, key)
    return text_of(value) if mu.pdf_is_string(value) else None
