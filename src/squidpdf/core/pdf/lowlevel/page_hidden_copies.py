"""The hidden copies a page's drawings keep (marked content), read and rewritten."""

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
    """The hidden copies a page's drawings keep, read and rewritten in their bytes."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def read(self, page: int) -> list[str]:
        """Every hidden copy on the page, as text, and each string MuPDF may miss."""
        mu = pymupdf.mupdf
        found: list[str] = []
        for streams, resources in self._drawings(page):
            for marking in values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)):
                found += _hidden_copies_in(marking)
            for stream in streams:
                found += self._hidden_copies_written_in(bytes_of(stream), resources)
        return found

    def rewrite(self, page: int, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy on the page; one blanked goes."""
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
        """Each drawing the page and its annotations draw, with the resources it names."""
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(pdf_of(self.doc), page)
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        # One stream, or a list of them drawn in turn.
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
        """Put `rewritten(hidden_copy)` for each hidden copy written in `stream`."""
        mu = pymupdf.mupdf
        drawing = bytes_of(stream)
        pieces: list[bytes] = []
        kept_to = 0  # where the drawing is still to be copied from
        for start, end, marking in self._markings_in(drawing, resources):
            # Not read whole, or an image: no reading is every reader's, so it stays.
            if not isinstance(marking, pymupdf.mupdf.PdfObj):
                continue
            # Read before the rewrite, which changes `marking`.
            lost_string = self._loses_a_string(marking, written=drawing[start:end])
            changed = _rewrite_hidden_copies_in(marking, rewritten)
            # Changed, or a string MuPDF drops: as MuPDF reads it, so no reader finds the other.
            if changed or lost_string:
                pieces += [drawing[kept_to:start], as_written(marking)]
                kept_to = end
        # Nothing changed: the stream stays as the file wrote it.
        if not pieces:
            return
        new_drawing = b"".join([*pieces, drawing[kept_to:]])
        mu.pdf_update_stream(
            pdf_of(self.doc), stream, mu.fz_new_buffer_from_copied_data(new_drawing), 0
        )

    def _hidden_copies_written_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> list[str]:
        """The hidden copies written in `drawing`, and each string in it MuPDF may miss.

        After a dictionary not read whole, or an image MuPDF can't read, that's every
        string to the end of the drawing, since nothing says where either ends.
        """
        found: list[str] = []
        unread_from = len(drawing)  # where the first one not read whole starts, if any
        for start, end, marking in self._markings_in(drawing, resources):
            written = drawing[start:end]
            # Not read whole: any string after its start may be a hidden copy.
            if marking is None:
                unread_from = min(unread_from, start)
                continue
            # An image: a reader ending it sooner than MuPDF reads on in its bytes.
            if isinstance(marking, InlineImage):
                found += strings_past_an_end_in(marking)
                continue
            # One MuPDF reads in part: a string it drops may be one another reader keeps.
            if self._loses_a_string(marking, written=written):
                found += strings_in(pdf_of(self.doc), written)
            # Those MuPDF reads, a string of its own included.
            found += _hidden_copies_in(marking)
        # From the first only: what follows a later one follows it too.
        rest = drawing[unread_from:]
        return found + strings_in(pdf_of(self.doc), rest) + strings_anywhere_in(rest)

    def _loses_a_string(self, marking: pymupdf.mupdf.PdfObj, *, written: bytes) -> bool:
        """Whether MuPDF's reading of a dictionary holds fewer strings than `written` does.

        As with a key written twice: MuPDF keeps the last, another reader the other.
        """
        # No string written in it, as in most (`<</MCID 0>>`): none to lose.
        if not may_hold_a_string(written):
            return False
        return len(strings_in(pdf_of(self.doc), as_written(marking))) < len(
            strings_in(pdf_of(self.doc), written)
        )

    def _markings_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> Iterator[tuple[int, int, pymupdf.mupdf.PdfObj | InlineImage | None]]:
        """Each marked content's dictionary and image in `drawing`, and where each is.

        Read by MuPDF's lexer, as it draws them. Every dictionary counts, as MuPDF keeps one
        even before its tag; one not read whole comes as None, as does all after an image MuPDF
        can't read. An image it reads comes too, since a reader ending it sooner reads in it.
        """
        mu = pymupdf.mupdf
        # No dictionary in it, as in most drawings.
        if b"<<" not in drawing:
            return
        reader, lexbuf = lexer_of(drawing)
        # With no BI anywhere, as in most, no operator need be read to find an image.
        may_hold_image = b"BI" in drawing
        for start, token in tokens_in(reader, lexbuf):
            # A dictionary: read to where it ends.
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
            # An image MuPDF can read: its bytes aren't drawing to it, so it reads on past them.
            if image is not None:
                yield start, mu.fz_tell(reader), image
                continue
            # One it can't: MuPDF reads no further, so the rest may hold anything.
            yield start, len(drawing), None
            return

    def _dictionary_in(
        self, reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
    ) -> pymupdf.mupdf.PdfObj | None:
        """The dictionary `reader` is in, read to its end; None unless it's read whole.

        Read whole: MuPDF's parser and a count of << and >> end at the same >>. If not,
        `reader` is left at the farther stop, though a string may still lie past both.
        """
        mu = pymupdf.mupdf
        opened = mu.fz_tell(reader)  # just past its <<
        # MuPDF's parser raises on a dictionary it can't read.
        try:
            dictionary = mu.pdf_parse_dict(pdf_of(self.doc), reader, lexbuf)
        except mu.FzErrorSyntax:
            dictionary = None
        parsed_to = mu.fz_tell(reader)
        # Read again, to the first >> that closes it by count.
        mu.fz_seek(reader, opened, 0)
        read_past_dictionary(reader, lexbuf)
        counted_to = mu.fz_tell(reader)
        # Read whole: the parser stopped there too.
        read_whole = dictionary is not None and counted_to == parsed_to
        if read_whole:
            return dictionary
        # Not whole: read on from the farther stop.
        mu.fz_seek(reader, max(parsed_to, counted_to), 0)
        return None


def _hidden_copies_in(marking: pymupdf.mupdf.PdfObj) -> list[str]:
    """The hidden copies a marked content's dictionary holds, as text."""
    texts = (_text_at(marking, key) for key in HIDDEN_COPY_KEYS)
    return [text for text in texts if text is not None]


def _rewrite_hidden_copies_in(
    marking: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
) -> bool:
    """Put `rewritten(hidden_copy)` for each hidden copy in `marking`; whether any changed."""
    changed = False
    for key in HIDDEN_COPY_KEYS:
        hidden_copy = _text_at(marking, key)
        # Not there, or not text.
        if hidden_copy is None:
            continue
        kept = rewritten(hidden_copy)
        if kept == hidden_copy:
            continue
        changed = True
        put_text(marking, key, kept)
    return changed


def _text_at(holder: pymupdf.mupdf.PdfObj, key: str) -> str | None:
    """The text a dictionary holds under `key`; None when it holds none there, or no text."""
    mu = pymupdf.mupdf
    value = mu.pdf_dict_gets(holder, key)
    return text_of(value) if mu.pdf_is_string(value) else None
