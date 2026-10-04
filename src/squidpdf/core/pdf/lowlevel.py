"""The only file that uses MuPDF's low-level API.

Everyday PyMuPDF calls (insert_text, get_pixmap, save) are easy to read, so
they stay in `core.pdf.mupdf`, whose driver holds a `PdfFile` for the rest. The
hard-to-read calls live here, and their results come back as named
dataclasses. This file only reports what the PDF says; the engine decides
what to do with it.
"""

from __future__ import annotations

import codecs
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.app.message import Message
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.types import FontCode, GlyphId, Rect

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255
_OPACITY_PREFIX = "SquidOpacity"  # our graphics states' names, e.g. SquidOpacity600 for 0.6
_PERMILLE = 1000  # opacity is written to a thousandth, far finer than the eye sees

_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# The keys of marked content's hidden copies.
_HIDDEN_COPY_KEYS = ("ActualText", "Alt", "E")
# Where an image written into a drawing ends, as MuPDF looks for it past the image's
# bytes: the first EI with a space, a line end, a < or a / after it, or nothing.
_INLINE_IMAGE_END = re.compile(rb"EI(?=[\x00-\x20</]|\Z)")
# The marks a string in UTF-16 starts with: big-endian, then little-endian.
_UTF16_MARKS = (codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)
# How a string that starts with each mark reads: in UTF-16, either way round, or in UTF-8.
_MARKED_CODECS = {
    codecs.BOM_UTF16_BE: "utf-16-be",
    codecs.BOM_UTF16_LE: "utf-16-le",
    codecs.BOM_UTF8: "utf-8",
}
# An escape in a string written in brackets: a backslash, then up to three octal digits,
# or a line end, or one byte.
_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(\r\n|.))", re.DOTALL)
# What such a string reads apart from its bytes: an escape, or a bracket, which opens a
# string in it or closes one.
_LITERAL_PART = re.compile(_ESCAPE.pattern + rb"|[()]", re.DOTALL)
_BRACKET_DEPTH = {b"(": 1, b")": -1}  # how each bracket changes how deep in strings it is
# What an escape stands for, by what follows its backslash, but octal digits: a line end
# stands for nothing, as the string goes on on the next line, and any byte not here, itself.
_ESCAPED = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"\r\n": b"",
    b"\r": b"",
    b"\n": b"",
}
# A string written in hex, from a < to the > that ends it: each < inside starts one too.
_HEX_STRING = re.compile(rb"<[^>]*")
# What a string written in hex reads past: anything but its digits.
_NOT_HEX = re.compile(rb"[^0-9A-Fa-f]")

# What PyMuPDF raises when MuPDF can't do what it was asked: MuPDF's own errors,
# which aren't RuntimeErrors, and the RuntimeErrors and ValueErrors PyMuPDF adds.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)
# MuPDF's own errors alone, which hold a pointer, so they can't cross between processes.
MUPDF_OWN_ERRORS = pymupdf.mupdf.FzErrorBase
# The one of them that means the work was too big, not the file broken: past MuPDF's limit.
MUPDF_TOO_HEAVY = pymupdf.mupdf.FzErrorLimit
# The one that means the machine failed, not the file: out of memory, or a file it can't open.
MUPDF_SYSTEM_ERRORS = pymupdf.mupdf.FzErrorSystem


@dataclass(frozen=True, slots=True, eq=False)
class PdfFile:
    """An open PDF, as MuPDF's low-level API sees it: a few plain methods over it."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first.

        None if the font has no letter list (its ToUnicode) or MuPDF can't read
        that list; DriverError if MuPDF can't load the font at all. `code_bytes`
        is 1 for a simple font, 2 for a Type0 (two-byte) font.
        """
        value_type, _value = self.doc.xref_get_key(xref, "ToUnicode")
        if value_type == _PDF_NULL:
            return None

        with self._mupdf_font_record(xref) as font:
            if font.to_unicode is None:  # MuPDF couldn't read it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Type0: one code per glyph, so stop after the last glyph
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (_font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` is read under, often the font file's own.

        A page can list a font as "Carlito Bold" while its text reads "Carlito-Bold",
        the name inside the file. None when MuPDF can't load the font.
        """
        try:
            with self._mupdf_font_record(xref) as font:
                return pymupdf.mupdf.ll_fz_font_name(font.font)
        except DriverError:  # MuPDF can't load the font
            return None

    @contextmanager
    def _mupdf_font_record(self, xref: int) -> Iterator[pymupdf.mupdf.pdf_font_desc]:
        """MuPDF's own record of font `xref`: its letter list, codes and glyph widths.

        MuPDF's C function pdf_load_font builds the record and pdf_drop_font
        frees it; Python never frees it, so it is only lent out inside a `with`.
        DriverError if MuPDF can't load the font.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, None, mu.pdf_load_object(pdf, xref).m_internal
            )
        except MUPDF_ERRORS as exc:  # MuPDF can't make sense of the font object
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        try:
            yield font
        finally:
            mu.ll_pdf_drop_font(font)

    def redact(self, page: int, boxes: list[Rect]) -> None:
        """Delete every letter whose box touches one of `boxes`, and any link or comment on it.

        A comment goes only when it writes its words on the page (a FreeText): a note
        behind an icon, a highlight or a form field stays.
        """
        pg = self.doc[page]
        for box in boxes:
            pg.add_redact_annot(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1))
        pg.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
        )

    @contextmanager
    def links_kept(self, page: int) -> Iterator[None]:
        """Each link the page loses inside the `with` comes back, as it was.

        The same object, in its place in the page's list of annotations (its Annots),
        so a link keeps its look and what it does, even what PyMuPDF can't describe.
        Any other annotation the page loses stays gone.
        """
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(self._pdf(), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        # The list as it was, a copy that holds on to each entry while MuPDF drops some.
        before = mu.pdf_copy_array(listed) if mu.pdf_is_array(listed) else None
        yield
        # No list before: nothing to put back.
        if before is None:
            return
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        # A redaction only deletes, so what's left keeps its order.
        place = 0  # where the next link goes back: after each entry before it still listed
        for entry in range(mu.pdf_array_len(before)):
            annotation = mu.pdf_array_get(before, entry)
            # Still listed: the next one goes after it.
            if mu.pdf_array_find(listed, annotation) >= 0:
                place += 1
                continue
            subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
            # Another kind of annotation, a comment: it stays gone.
            if not mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Link):
                continue
            mu.pdf_array_insert(listed, annotation, place)
            place += 1
        self._sync_links(page)

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`, whatever it does.

        Read from the page's own list of annotations (its Annots): PyMuPDF's list
        of links leaves out what MuPDF can't follow, a script or a form reset.
        """
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(self._pdf(), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        to_page = ~self._to_pdf_matrix(page)
        boxes_shown = [pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) for box in boxes]
        # From the end, so each deletion leaves the places still to read where they were.
        for place in reversed(range(mu.pdf_array_len(listed))):
            annotation = mu.pdf_array_get(listed, place)
            subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
            # Another kind of annotation: a note, a form field.
            if not mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Link):
                continue
            rect = mu.pdf_to_rect(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Rect))
            link_area = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * to_page
            if any(link_area.intersects(box) for box in boxes_shown):
                mu.pdf_array_delete(listed, place)
        self._sync_links(page)

    def hidden_copies(self, page: int) -> list[str]:
        """Every hidden copy on the page, as text: those `rewrite_hidden_copies` reaches.

        And each string written in a marked content's dictionary MuPDF reads
        only in part (it keeps the last of a key written twice); and every
        string from a dictionary not read whole (`_dictionary_in`), or from an
        image written into a drawing that MuPDF can't read, to the end of that
        drawing, read from every place one may start (`_strings_anywhere_in`):
        any may be one another reader keeps, and a word left in it is a leak.
        """
        mu = pymupdf.mupdf
        found: list[str] = []
        for streams, resources in self._drawings(page):
            for marking in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)):
                found += _hidden_copies_in(marking)
            for stream in streams:
                found += self._hidden_copies_written_in(_bytes_of(stream), resources)
        return found

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy on the page; one left blank goes.

        One in a dictionary not read whole (`_dictionary_in`), or after an image MuPDF
        can't read, stays as written. A dictionary with a key written twice is written
        back as MuPDF reads it: the last.
        """
        mu = pymupdf.mupdf
        for streams, resources in self._drawings(page):
            properties = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)
            for marking in _values_of(properties):
                _rewrite_hidden_copies_in(marking, rewritten)
            for stream in streams:
                self._rewrite_written(stream, resources, rewritten)

    def _drawings(
        self, page: int
    ) -> Iterator[tuple[list[pymupdf.mupdf.PdfObj], pymupdf.mupdf.PdfObj]]:
        """The page's drawing, its annotations' appearances, each drawing they draw.

        Each with the resources it names things from, which may be none.
        """
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(self._pdf(), page)
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        # One stream, or a list of them drawn one after the other.
        streams = _items_of(contents) if mu.pdf_is_array(contents) else [contents]
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        waiting = [(streams, resources)] + [
            ([appearance], _resources_of(appearance))
            for appearance in _appearances_of(page_obj)
        ]
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

    def _rewrite_written(
        self,
        stream: pymupdf.mupdf.PdfObj,
        resources: pymupdf.mupdf.PdfObj,
        rewritten: Callable[[str], str],
    ) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy written in `stream`.

        `resources` are those it names things from, as an image's colour space.
        """
        mu = pymupdf.mupdf
        drawing = _bytes_of(stream)
        pieces: list[bytes] = []
        kept_to = 0  # where the drawing is still to be copied from
        for start, end, marking in self._markings_in(drawing, resources):
            # Not read whole: no reading is every reader's, so it stays, for the check to read.
            if marking is None:
                continue
            # Read before the rewrite, which changes `marking`.
            lost_string = self._loses_a_string(marking, written=drawing[start:end])
            changed = _rewrite_hidden_copies_in(marking, rewritten)
            if changed or lost_string:
                pieces += [drawing[kept_to:start], _as_written(marking)]
                kept_to = end
        # Nothing changed: the stream stays as the file wrote it.
        if not pieces:
            return
        new_drawing = b"".join([*pieces, drawing[kept_to:]])
        mu.pdf_update_stream(
            self._pdf(), stream, mu.fz_new_buffer_from_copied_data(new_drawing), 0
        )

    def _hidden_copies_written_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> list[str]:
        """The hidden copies written in `drawing`, as `hidden_copies` reads them.

        The hidden copies MuPDF reads in each marked content's dictionary,
        which may be strings of their own; and each string written in one it
        reads only in part (it keeps the last of a key written twice). After a
        dictionary not read whole, or an image MuPDF can't read, every string
        to the end of the drawing, since no reading says where it ends: as
        MuPDF's lexer reads them, and from every place one may start, since
        another reader may read the bytes before it otherwise. Only there: any
        other string, as a language (`/Lang (en-US)`), is none.
        """
        found: list[str] = []
        unread_from = len(drawing)  # where the first one not read whole starts, if any
        for start, end, marking in self._markings_in(drawing, resources):
            written = drawing[start:end]
            # Not read whole: it may end anywhere, so any string after its start may be one.
            if marking is None:
                unread_from = min(unread_from, start)
                continue
            # One MuPDF reads in part: a string it drops may be one another reader keeps.
            if self._loses_a_string(marking, written=written):
                found += self._strings_in(written)
            # Those MuPDF reads, which may be strings of their own, kept apart.
            found += _hidden_copies_in(marking)
        # Read once, from the first: what follows a later one is part of what follows it.
        rest = drawing[unread_from:]
        return found + self._strings_in(rest) + _strings_anywhere_in(rest)

    def _strings_in(self, written: bytes) -> list[str]:
        """Each string in `written`, part of a drawing, as text."""
        mu = pymupdf.mupdf
        reader = mu.fz_open_buffer(mu.fz_new_buffer_from_copied_data(written))
        lexbuf = mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)  # as `_markings_in` has it
        found: list[str] = []
        while True:
            start = mu.fz_tell(reader)
            token = mu.pdf_lex(reader, lexbuf)
            # The end of what's written.
            if token == mu.PDF_TOK_EOF:
                return found
            # A string: read again from its start, as an object, for its text.
            if token == mu.PDF_TOK_STRING:
                mu.fz_seek(reader, start, 0)
                string = mu.pdf_parse_stm_obj(self._pdf(), reader, lexbuf)
                found.append(_text_of(string))

    def _loses_a_string(self, marking: pymupdf.mupdf.PdfObj, *, written: bytes) -> bool:
        """Whether MuPDF's reading of a dictionary, written as `written`, holds fewer strings.

        MuPDF keeps only the last of a key written twice, and the other may be a
        hidden copy that another reader keeps.
        """
        return len(self._strings_in(_as_written(marking))) < len(self._strings_in(written))

    def _markings_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> Iterator[tuple[int, int, pymupdf.mupdf.PdfObj | None]]:
        """Each marked content's dictionary in `drawing`: where it starts and ends, read.

        Every dictionary written in a drawing is one, whatever operator comes
        after it: MuPDF keeps one as what marked content says even before its
        tag (`<<...>> /Span BDC`). An image's own dictionary, the other kind,
        is read past with the image. Read by MuPDF's own reader of a drawing
        (its lexer), so strings, comments and images written into it read as
        MuPDF draws them. One not read whole (`_dictionary_in`) comes as None,
        from where it starts to the farther of its two ends; so does all that
        follows an image MuPDF can't read. `resources` are those the drawing
        names things from.
        """
        mu = pymupdf.mupdf
        # No dictionary written in it, so none that marks content: most drawings are so.
        if b"<<" not in drawing:
            return
        reader = mu.fz_open_buffer(mu.fz_new_buffer_from_copied_data(drawing))
        # Small: the wrapper holds no more, so large overruns it; it grows for a long string.
        lexbuf = mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)
        while True:
            start = mu.fz_tell(reader)
            token = mu.pdf_lex(reader, lexbuf)
            # The end of the drawing.
            if token == mu.PDF_TOK_EOF:
                return
            # A dictionary: read to where it ends.
            if token == mu.PDF_TOK_OPEN_DICT:
                dictionary = self._dictionary_in(reader, lexbuf)
                yield start, mu.fz_tell(reader), dictionary
                continue
            # An operator is the last word read, past any space or comment before it.
            is_operator = token == mu.PDF_TOK_KEYWORD
            operator = drawing[start : mu.fz_tell(reader)].split()[-1] if is_operator else b""
            # Not an image written into the drawing.
            if operator != b"BI":
                continue
            # An image MuPDF can read: its bytes aren't drawing, and it reads on after them.
            if self._read_past_image(reader, lexbuf, drawing=drawing, resources=resources):
                continue
            # One it can't: it reads no further, and what's left may hold anything.
            yield start, len(drawing), None
            return

    def _dictionary_in(
        self, reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
    ) -> pymupdf.mupdf.PdfObj | None:
        """The dictionary `reader` is in, read to its end; None unless it's read whole.

        Read whole: MuPDF's parser and a count of << and >> end at the same >>.
        They part where the parser stops early, where it can't read on, maybe
        before strings still in it: raising, as at a key with no value, or not,
        at an ID where a key goes, as an image's dictionary ends
        (`<</ActualText (Logo) ID ...>>`). They part too at a >> inside an
        array (`<</A [ >> ] /ActualText (...)>>`): the parser reads it as an
        item and reads on, but another reader may end the dictionary there, as
        the count does. Not read whole, `reader` is left at the farther stop,
        but both may stop before a string still in it, so where it ends is no
        reading's to say.
        """
        mu = pymupdf.mupdf
        opened = mu.fz_tell(reader)  # just past its <<
        # MuPDF's parser raises on a dictionary it can't read, as one with a key and no value.
        try:
            dictionary = mu.pdf_parse_dict(self._pdf(), reader, lexbuf)
        except mu.FzErrorSyntax:
            dictionary = None
        parsed_to = mu.fz_tell(reader)  # where the parser stopped
        # Read again, to the first >> that closes it by count.
        mu.fz_seek(reader, opened, 0)
        _read_past_dictionary(reader, lexbuf)
        counted_to = mu.fz_tell(reader)
        # Read whole: the parser stopped there too.
        read_whole = dictionary is not None and counted_to == parsed_to
        if read_whole:
            return dictionary
        # Not whole: read on from the farther stop.
        mu.fz_seek(reader, max(parsed_to, counted_to), 0)
        return None

    def _read_past_image(
        self,
        reader: pymupdf.mupdf.FzStream,
        lexbuf: pymupdf.mupdf.PdfLexbuf,
        *,
        drawing: bytes,
        resources: pymupdf.mupdf.PdfObj,
    ) -> bool:
        """Read past an image written into `drawing`, as MuPDF draws it; whether it can.

        `reader` is just past its BI. MuPDF reads its dictionary, up to ID; its
        bytes, as many as the dictionary says or its filter takes; then the EI
        after them. `resources` are those it names its colour space from.
        """
        mu = pymupdf.mupdf
        # MuPDF raises on an image it can't read: its dictionary, colour space or bytes.
        try:
            dictionary = mu.pdf_parse_dict(self._pdf(), reader, lexbuf)
            # A carriage return and a line feed after ID are one line end, then the bytes.
            after_id = mu.fz_read_byte(reader)
            two_byte_end = after_id == ord("\r") and mu.fz_peek_byte(reader) == ord("\n")
            if two_byte_end:
                mu.fz_read_byte(reader)
            _load_inline_image(self._pdf(), dictionary, reader=reader, resources=resources)
        except mu.FzErrorSyntax, mu.FzErrorFormat:
            return False
        end = _INLINE_IMAGE_END.search(drawing, mu.fz_tell(reader))
        # No EI after its bytes: MuPDF reads no further.
        if end is None:
            return False
        mu.fz_seek(reader, end.end(), 0)
        return True

    def restore_font(self, page: int, resource: str, xref: int) -> None:
        """Point the page's resource name `resource` at font `xref`, already in the file.

        Erasing text can remove a font the page no longer uses: this puts it
        back. Nothing new is added to the file.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        # An empty m_internal means the key isn't there yet, so make it.
        if not resources.m_internal:
            resources = mu.pdf_dict_put_dict(page_obj, mu.PDF_ENUM_NAME_Resources, 1)
        fonts = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Font)
        if not fonts.m_internal:
            fonts = mu.pdf_dict_put_dict(resources, mu.PDF_ENUM_NAME_Font, 1)
        mu.pdf_dict_puts(fonts, resource, mu.pdf_new_indirect(pdf, xref, 0))

    def add_opacity(self, page: int, opacity: float) -> str:
        """The page's name for a graphics state that fills at `opacity`, written to the page.

        Rounded to a thousandth and named for it, so writing it again changes nothing.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        permille = round(opacity * _PERMILLE)
        name = f"{_OPACITY_PREFIX}{permille}"
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        # An empty m_internal means the key isn't there yet, so make it.
        if not resources.m_internal:
            resources = mu.pdf_dict_put_dict(page_obj, mu.PDF_ENUM_NAME_Resources, 1)
        states = mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_ExtGState)
        if not states.m_internal:
            states = mu.pdf_dict_put_dict(resources, mu.PDF_ENUM_NAME_ExtGState, 1)
        state = mu.pdf_new_dict(pdf, 1)
        mu.pdf_dict_put_real(state, mu.PDF_ENUM_NAME_ca, permille / _PERMILLE)
        mu.pdf_dict_puts(states, name, state)
        return name

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page as you see it into the PDF's own coordinates.

        Not page.transformation_matrix: on a turned page it forgets where the
        page's box starts.
        """
        moved = pymupdf.Point(*point) * self._to_pdf_matrix(page)
        return (moved.x, moved.y)

    def _to_pdf_matrix(self, page: int) -> pymupdf.Matrix:
        """What turns a point on the page as you see it into the PDF's own coordinates."""
        mu = pymupdf.mupdf
        pg = self.doc[page]
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(mu.pdf_page_from_fz_page(pg.this), _mediabox, page_to_screen)
        return pg.rotation_matrix * ~pymupdf.Matrix(page_to_screen)

    def _sync_links(self, page: int) -> None:
        """Have MuPDF read the page's links again: it keeps a list of its own, read once."""
        pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(self.doc[page].this)
        pymupdf.mupdf.pdf_sync_links(pdf_page)

    def _pdf(self) -> pymupdf.mupdf.PdfDocument:
        """The same document, as MuPDF's low-level API needs it."""
        return pymupdf.mupdf.pdf_document_from_fz_document(self.doc.this)


def _font_code(font: pymupdf.mupdf.pdf_font_desc, value: int) -> FontCode | None:
    """What code `value` draws in a font MuPDF has loaded, or None if it isn't one letter."""
    mu = pymupdf.mupdf
    codepoint = mu.ll_pdf_lookup_cmap(font.to_unicode, value)
    # No letter, or several (like "fi").
    if not 0 <= codepoint <= sys.maxunicode:
        return None
    # The font's internal number for the code, which picks its shape and width.
    cid = mu.ll_pdf_lookup_cmap(font.encoding, value)
    return FontCode(
        value=value,
        letter=chr(codepoint),
        glyph=GlyphId(mu.ll_pdf_font_cid_to_gid(font, cid)),
        width=mu.ll_pdf_lookup_hmtx(font, cid).w,
    )


def _values_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each value in a PDF dictionary; none when it isn't one."""
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_val(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def _appearances_of(page: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each appearance stream of each annotation on the page: plain, hovered and pressed.

    An annotation's appearance is the drawing it shows on the page, kept in a
    stream of its own.
    """
    mu = pymupdf.mupdf
    for annotation in _items_of(mu.pdf_dict_get(page, mu.PDF_ENUM_NAME_Annots)):
        for appearance in _values_of(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_AP)):
            # One stream, or one for each state, as a checkbox's on and off.
            yield from [appearance] if mu.pdf_is_stream(appearance) else _values_of(appearance)


# A drawing's stream, and the resources it names things from.
type _Drawing = tuple[pymupdf.mupdf.PdfObj, pymupdf.mupdf.PdfObj]


def _drawn_by(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each drawing `resources` names, each kept in a stream of its own."""
    yield from _forms_in(resources)
    yield from _tiling_patterns_in(resources)
    yield from _soft_masks_in(resources)
    yield from _type3_letters_in(resources)


def _forms_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each form (XObject) `resources` names; an image holds no drawing."""
    mu = pymupdf.mupdf
    for xobject in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_XObject)):
        subtype = mu.pdf_dict_get(xobject, mu.PDF_ENUM_NAME_Subtype)
        # A form, not an image.
        if mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Form):
            yield xobject, _resources_of(xobject)


def _tiling_patterns_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each tiling pattern `resources` names; a shading pattern holds no drawing."""
    mu = pymupdf.mupdf
    for pattern in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Pattern)):
        # A tiling pattern, not a shading one.
        if mu.pdf_is_stream(pattern):
            yield pattern, _resources_of(pattern)


def _soft_masks_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """The drawing of each soft mask the graphics states in `resources` name."""
    mu = pymupdf.mupdf
    for state in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_ExtGState)):
        mask = mu.pdf_dict_get(state, mu.PDF_ENUM_NAME_SMask)
        group = mu.pdf_dict_get(mask, mu.PDF_ENUM_NAME_G)
        # No soft mask, or /None.
        if mu.pdf_is_stream(group):
            yield group, _resources_of(group)


def _type3_letters_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each letter of each Type3 font `resources` names, with its font's resources."""
    mu = pymupdf.mupdf
    for font in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Font)):
        subtype = mu.pdf_dict_get(font, mu.PDF_ENUM_NAME_Subtype)
        # A Type3 font, whose letters are drawings.
        if mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Type3):
            letters = mu.pdf_dict_get(font, mu.PDF_ENUM_NAME_CharProcs)
            yield from ((letter, _resources_of(font)) for letter in _values_of(letters))


def _resources_of(holder: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    """The resources a drawing or a Type3 font names things from; null if none."""
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(holder, mu.PDF_ENUM_NAME_Resources)


def _items_of(array: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each item in a PDF array."""
    mu = pymupdf.mupdf
    return [mu.pdf_array_get(array, place) for place in range(mu.pdf_array_len(array))]


def _bytes_of(stream: pymupdf.mupdf.PdfObj) -> bytes:
    """A stream's bytes, decoded."""
    mu = pymupdf.mupdf
    # A copy: a stream changed since the file was read lends its own buffer, and
    # taking that buffer's bytes would empty the stream.
    return mu.fz_buffer_extract_copy(mu.pdf_load_stream(stream))


def _read_past_dictionary(
    reader: pymupdf.mupdf.FzStream, lexbuf: pymupdf.mupdf.PdfLexbuf
) -> None:
    """Read on past the >> that closes the dictionary `reader` is in, or to the end if none.

    Counts only << and >>: a >> inside an array closes it here, though MuPDF
    reads that as an item and reads on.
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


def _load_inline_image(
    pdf: pymupdf.mupdf.PdfDocument,
    dictionary: pymupdf.mupdf.PdfObj,
    *,
    reader: pymupdf.mupdf.FzStream,
    resources: pymupdf.mupdf.PdfObj,
) -> None:
    """Read an image's bytes from `reader`, as MuPDF does drawing it (pdf_load_inline_image).

    MuPDF takes the resources as a stack (pdf_resource_stack); here, one of one.
    """
    mu = pymupdf.mupdf
    stack = mu.pdf_resource_stack()  # held here: the wrapper below only points at it
    stack.resources = resources.m_internal
    stack.next = None
    mu.pdf_load_inline_image(pdf, mu.PdfResourceStack(stack), dictionary, reader)


def _as_written(value: pymupdf.mupdf.PdfObj) -> bytes:
    """A dictionary or a string as MuPDF writes it into a drawing: closed up, in ASCII."""
    mu = pymupdf.mupdf
    written = mu.fz_new_buffer(0)  # grows as it's written
    output = mu.FzOutput(written)
    mu.pdf_print_obj(output, value, 1, 1)  # closed up, and ASCII
    mu.fz_close_output(output)
    return mu.fz_buffer_extract(written)


def _text_of(string: pymupdf.mupdf.PdfObj) -> str:
    """A string's text, all of it, each NUL in it read as a space.

    MuPDF's own reading stops at a NUL, but in UTF-16. A NUL means nothing in
    text, so it reads as what parts two words.
    """
    mu = pymupdf.mupdf
    # A string of its own, an object of the file: written, it would read as its reference.
    string = mu.pdf_resolve_indirect(string)
    written = _as_written(string)
    # Written as text, it's all printable, so it holds no NUL: MuPDF writes any other as hex.
    if not written.startswith(b"<"):
        return mu.pdf_to_text_string(string)
    held = bytes.fromhex(written[1:-1].decode())
    # UTF-16, which MuPDF reads past a NUL.
    if held[:2] in _UTF16_MARKS:
        return mu.pdf_to_text_string(string).replace("\0", " ")
    # Any other stops at one: read again, with a space for each.
    return mu.pdf_to_text_string(_string_of(held.replace(b"\0", b" ")))


def _string_of(held: bytes) -> pymupdf.mupdf.PdfObj:
    """A PDF string holding exactly these bytes."""
    mu = pymupdf.mupdf
    # Read from them written as hex: MuPDF's own maker of a string takes text, not bytes.
    written = mu.fz_new_buffer_from_copied_data(b"<" + held.hex().encode() + b">")
    lexbuf = mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)  # as `_markings_in` has it
    # A string belongs to no file, so it's read without one.
    return mu.pdf_parse_stm_obj(mu.PdfDocument(), mu.fz_open_buffer(written), lexbuf)


def _strings_anywhere_in(written: bytes) -> list[str]:
    """Each string in `written`, part of a drawing, as text, wherever a reader may start one.

    A string starts at a ( or a <, and a reader that reads the bytes before
    one otherwise than MuPDF (as an image's bytes, or as a comment) may start
    at any of them. So each is read: in brackets (`_literal_strings_in`) and
    in hex (`_hex_strings_in`), each as text, and from each mark of UTF-16 or
    UTF-8 in it (`_texts_of`).
    """
    held = _literal_strings_in(written) + _hex_strings_in(written)
    return [text for string in held for text in _texts_of(string)]


def _literal_strings_in(written: bytes) -> list[bytes]:
    """The bytes of each string in brackets in `written`, from each (, once.

    A string starting at a ( inside another ends inside it, no later, so its
    bytes are part of that one's, a bracket either side: each is read from
    the first ( past the end of the one before. Each with the ) that closes
    it, which reads as no letter; one never closed reads to the end, as a
    reader that forgives it reads it.
    """
    found: list[bytes] = []
    start = written.find(b"(")
    while start != -1:
        end = _literal_end(written, start)
        found.append(_ESCAPE.sub(_unescaped, written[start + 1 : end]))
        start = written.find(b"(", end)
    return found


def _literal_end(written: bytes, start: int) -> int:
    """Just past the ) that closes the string in brackets at `written[start]`, or the end."""
    depth = 0  # strings open: the one at `start`, and each in it
    for part in _LITERAL_PART.finditer(written, start):
        depth += _BRACKET_DEPTH.get(part[0], 0)
        # Closed: back out of the one at `start`.
        if depth == 0:
            return part.end()
    return len(written)


def _unescaped(escape: re.Match[bytes]) -> bytes:
    """What an escape in a string in brackets stands for: a byte, or nothing for a line end."""
    octal, other = escape.groups()
    # Octal digits: the byte they number, past 255 wrapping, as MuPDF and pdf.js read it.
    if octal is not None:
        return bytes([int(octal, 8) % 256])
    return _ESCAPED.get(other, other)


def _hex_strings_in(written: bytes) -> list[bytes]:
    """The bytes of each string in hex in `written`, from each <.

    Each is read from its first < to its >, on past a < inside, as MuPDF
    and pdf.js read on past what's no digit. A reader that starts at a <
    inside reads a tail of those digits, so reading them from the first
    digit and from the second holds its bytes too. The digits from each <
    up to the next are read as well.
    """
    found: list[bytes] = []
    for string in _HEX_STRING.finditer(written):
        whole = string[0]
        # From the first <, then from each alone: with one <, the two are one.
        parts = [whole, *whole.split(b"<")[1:]]
        for digits in dict.fromkeys(_NOT_HEX.sub(b"", part) for part in parts):
            found += [_bytes_of_hex(digits[skip:]) for skip in (0, 1)]
    return found


def _bytes_of_hex(digits: bytes) -> bytes:
    """The bytes hex digits write, a last digit alone read with a 0 after it, as readers do."""
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())


def _texts_of(held: bytes) -> list[str]:
    """The text of a string holding `held`; and from each mark of UTF-16 or UTF-8 in it.

    A string that starts inside it, at a mark, reads in that mark's encoding.
    So `held` is read in it too, from each of the two bytes a letter of
    UTF-16 may start on; and again cut at each ), where such a string ends,
    so a letter it makes with the bytes after it can't join a word. A NUL
    reads as a space, as `_text_of` reads one.
    """
    texts = [_text_of(_string_of(held))]
    for mark, codec in _MARKED_CODECS.items():
        # No string in it starts with this mark.
        if mark not in held:
            continue
        pieces = [held, *held.split(b")")]
        texts += [
            piece[skip:].decode(codec, "replace").replace("\0", " ")
            for piece in pieces
            for skip in (0, 1)
        ]
    return texts


def _hidden_copies_in(marking: pymupdf.mupdf.PdfObj) -> list[str]:
    """The hidden copies a marked content's dictionary holds, as text."""
    mu = pymupdf.mupdf
    values = [mu.pdf_dict_gets(marking, key) for key in _HIDDEN_COPY_KEYS]
    return [_text_of(value) for value in values if mu.pdf_is_string(value)]


def _rewrite_hidden_copies_in(
    marking: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
) -> bool:
    """Put `rewritten(hidden_copy)` for each hidden copy in `marking`; whether any changed."""
    mu = pymupdf.mupdf
    changed = False
    for key in _HIDDEN_COPY_KEYS:
        value = mu.pdf_dict_gets(marking, key)
        # Not there, or not text.
        if not mu.pdf_is_string(value):
            continue
        hidden_copy = _text_of(value)
        kept = rewritten(hidden_copy)
        # As it was: it stays as the file wrote it.
        if kept == hidden_copy:
            continue
        changed = True
        # Words of its own left: they stay.
        if kept:
            mu.pdf_dict_puts(marking, key, mu.pdf_new_text_string(kept))
            continue
        # Nothing left: the key goes.
        mu.pdf_dict_dels(marking, key)
    return changed
