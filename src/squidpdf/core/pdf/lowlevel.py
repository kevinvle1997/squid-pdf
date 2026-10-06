"""The only file that uses MuPDF's low-level API.

Everyday PyMuPDF calls (insert_text, get_pixmap, save) are easy to read, so
they stay in `core.pdf.mupdf`, whose driver holds a `PdfFile` for the rest. The
hard-to-read calls live here, and their results come back as named
dataclasses. This file only reports what the PDF says; the engine decides
what to do with it.
"""

from __future__ import annotations

import codecs
import ctypes
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Literal, assert_never
from xml.dom import minidom
from xml.parsers.expat import ExpatError

import pymupdf

from squidpdf.core.app.message import Message
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.types import CopyPlace, FontCode, GlyphId, HiddenCopy, Rect

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255
_OPACITY_PREFIX = "SquidOpacity"  # our graphics states' names, e.g. SquidOpacity600 for 0.6
_PERMILLE = 1000  # opacity is written to a thousandth, far finer than the eye sees

_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it
# A number, or a bracket, in a two-byte font's width list (`/W`).
_W_TOKEN = re.compile(r"\[|\]|-?\d+(?:\.\d+)?")
_RUN_TOKENS = 3  # `first last w`: a run of CIDs all one width
_TWO_BYTE_CODES = 65536  # a two-byte font has codes 0-65535
_WHOLE_NUMBER = re.compile(r"-?\d+")
_IDENTITY_ENCODINGS = ("/Identity-H", "/Identity-V")  # a two-byte font whose codes are its CIDs
# An object reference, `12 0 R`: the number is what's kept.
_OBJECT_REFERENCE = re.compile(r"(\d+) \d+ R")

# The keys of marked content's hidden copies.
_HIDDEN_COPY_KEYS = ("ActualText", "Alt", "E")
# The EI that ends an image written into a drawing, as MuPDF finds it past the bytes.
_INLINE_IMAGE_END = re.compile(rb"EI(?=[\x00-\x20</]|\Z)")
# The marks a string in UTF-16 starts with.
_UTF16_MARKS = (codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)
# The encoding a string reads in, by the mark it starts with.
_MARKED_CODECS = {
    codecs.BOM_UTF16_BE: "utf-16-be",
    codecs.BOM_UTF16_LE: "utf-16-le",
    codecs.BOM_UTF8: "utf-8",
}
# An escape in a string in brackets: octal digits, a line end, or one byte.
_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(\r\n|.))", re.DOTALL)
# What a string in brackets reads apart from its bytes: an escape, or a bracket.
_LITERAL_PART = re.compile(_ESCAPE.pattern + rb"|[()]", re.DOTALL)
_BRACKET_DEPTH = {b"(": 1, b")": -1}  # how each bracket changes how deep in strings it is
# What an escape stands for, by what follows the backslash, but octal digits; any other, itself.
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
# A string written in hex, from a < up to the > that ends it.
_HEX_STRING = re.compile(rb"<[^>]*")
# What a string written in hex reads past: anything but its digits.
_NOT_HEX = re.compile(rb"[^0-9A-Fa-f]")
# Printable ASCII and white space, no escape (\) or hex string (<): its strings read as written.
_PLAIN_DRAWING = re.compile(rb"[^\\<\x00-\x08\x0b\x0e-\x1f\x7f-\xff]*")

# What the file says about itself (its Info) that says when, not what.
_INFO_DATES = ("CreationDate", "ModDate")
_INFO_TITLE = "Title"  # its title, which a viewer shows in place of the file's name
_BOOKMARK_TITLE = "Title"  # what a bookmark reads
# What leads on from a bookmark: the next at its level, then the first under it.
_NEXT_BOOKMARKS = ("Next", "First")
_FIELD_KIDS = ("Kids",)  # what leads on from a form field: the fields and parts under it
_TAG_KIDS = ("K",)  # what leads on from a tag: the tags and content under it
_FIELD_VALUE = "V"  # a form field's value
_FIELD_RESET = "DV"  # what a form field goes back to when the form is reset
_FIELD_FORMATTED = "RV"  # a text field's value again, formatted, as XHTML
_FIELD_OPTIONS = "Opt"  # a choice field's options, each its words or a pair of them
_FIELD_DESCRIPTION = "TU"  # a form field's description, which a screen reader reads for it
# A signature field's seed values: what a signature put in it must be.
_FIELD_SEED_VALUES = "SV"
# An annotation's words: a comment's, or a form field's part's description of itself.
_ANNOTATION_WORDS = "Contents"
_COMMENT_LABELS = ("T", "Subj")  # a comment's author and its subject
_FORMATTED_WORDS = "RC"  # a comment's words again, formatted, as XHTML
_FIELD_CHOSEN = "I"  # a choice field's chosen options, by their places in its list
_FORM_XFA = "XFA"  # the form again, as XML (XFA), which some viewers show in its place
_APPEARANCE = "AP"  # an annotation's drawing (its appearance), kept apart from the page's
# A form field's part's captions (in its MK): as it rests, hovered and pressed.
_CAPTIONS = ("CA", "RC", "AC")
# The namespaces whose attributes XML and RDF read themselves, not text.
_XML_OWN = (
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
)
# The packet's wrapper around metadata (XMP), a note to a program; it holds no words.
_XMP_PACKET = "xpacket"
# XMP properties whose values are typed, not words, by namespace: rewritten, they'd be invalid.
_XMP_TYPED = {
    "http://purl.org/dc/elements/1.1/": ("format",),
    "http://ns.adobe.com/pdf/1.3/": ("PDFVersion", "Trapped"),
    "http://www.aiim.org/pdfa/ns/id/": ("part", "conformance", "amd", "rev"),
    "http://www.aiim.org/pdfua/ns/id/": ("part", "amd", "rev"),
    "http://ns.adobe.com/xap/1.0/t/pg/": (
        "NPages",
        "HasVisibleTransparency",
        "HasVisibleOverprint",
    ),
    "http://ns.adobe.com/xap/1.0/rights/": ("Marked",),
    "http://ns.adobe.com/xap/1.0/": ("CreateDate", "ModifyDate", "MetadataDate", "Rating"),
    "http://ns.adobe.com/xap/1.0/mm/": (
        "DocumentID",
        "InstanceID",
        "OriginalDocumentID",
        "VersionID",
    ),
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": (
        "documentID",
        "instanceID",
        "originalDocumentID",
        "versionID",
    ),
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": ("instanceID", "when"),
}
# How deep metadata (XMP) is read as XML; deeper goes whole. XMP nests a dozen or so, and
# reading or writing it takes a call per level, which Python limits.
_XML_DEEPEST = 100
# The encodings XMP may use; what isn't read as XML can't say which, so it's read in each.
_XMP_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
# What a signed file's catalog keeps only for its signatures, so it goes with them.
_SIGNATURE_CHECKS = ("Perms", "DSS")
# How a hidden copy goes back into the file once rewritten (`_rewrite_entry`).
type _Writing = Literal["text", "required_text", "whole", "field_value", "options", "drawing"]
# The copies a form field shows on the page: once one changes, the field is drawn again.
_FIELD_SHOWN: tuple[_Writing, ...] = ("field_value", "options")

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

    def listed_widths(self, xref: int) -> dict[str, float] | None:
        """Each letter the font's width list gives a width, per 1000 em, lowest code first.

        None when it has no list: `/Widths` for a simple font, `/W` on a two-byte font's
        one inside. A width of 0 is a code the file never draws, and is left out.
        """
        listed = self._listed_codes(xref)
        if listed is None:
            return None
        widths: dict[str, float] = {}
        with self._mupdf_font_record(xref) as font:
            for code in listed:
                cid = pymupdf.mupdf.ll_pdf_lookup_cmap(font.encoding, code)
                letter = _letter_of(font, code, cid)
                width = pymupdf.mupdf.ll_pdf_lookup_hmtx(font, cid).w
                if letter is not None and width > 0:
                    widths.setdefault(letter, width)  # lowest code first: the one a page writes
        return widths

    def _listed_codes(self, xref: int) -> range | list[int] | None:
        """The codes the font's width list gives widths for; None without one that reads."""
        # A simple font: its list runs from FirstChar to LastChar, within its byte.
        if self._entry(xref, "Widths") is not None:
            first = _whole_number(self._entry(xref, "FirstChar"))
            last = _whole_number(self._entry(xref, "LastChar"))
            if first is None or last is None:
                return None
            return range(max(first, 0), min(last, _ONE_BYTE_CODES - 1) + 1)
        descendants = self._entry(xref, "DescendantFonts")
        # Neither kind of list: the reader falls back on widths of its own.
        if descendants is None:
            return None
        # `/W` lists CIDs: only by Identity is a code its CID, so its letter can be read.
        if self._entry(xref, "Encoding") not in _IDENTITY_ENCODINGS:
            return None
        inner = _OBJECT_REFERENCE.search(descendants)
        # The one inside written in place, not as an object of its own: rare, and not read.
        if inner is None or "<<" in descendants:
            return None
        w = self._entry(int(inner.group(1)), "W")
        return None if w is None else _cids_in(w)

    def _entry(self, xref: int, key: str) -> str | None:
        """An object's entry as written, a reference to another followed; None when absent."""
        value_type, value = self.doc.xref_get_key(xref, key)
        if value_type == _PDF_NULL:
            return None
        # Kept in an object of its own, `9 0 R`: what that object holds.
        referenced = _OBJECT_REFERENCE.search(value) if value_type == "xref" else None
        if referenced is not None:
            return self.doc.xref_object(int(referenced.group(1)), compressed=True)
        return value

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

        Only a comment that writes its words on the page (a FreeText) goes. A redaction
        mark the file already holds stays unapplied: applying it is not this edit's call.
        """
        mu = pymupdf.mupdf
        # MuPDF applies every mark on the page, so only ours may be there.
        with self._file_marks_aside(page):
            # Unturned: a box is read unturned, and MuPDF places a mark as it's shown.
            with self._unturned(page) as pdf_page:
                # MuPDF's own calls, as PyMuPDF's read every mark on the page again per box.
                for box in boxes:
                    mark = mu.pdf_create_annot(pdf_page, mu.PDF_ANNOT_REDACT)
                    mu.pdf_set_annot_rect(mark, mu.FzRect(box.x0, box.y0, box.x1, box.y1))
            options = mu.PdfRedactOptions()
            options.black_boxes = 0  # nothing drawn where the letters were
            options.text = mu.PDF_REDACT_TEXT_REMOVE
            options.image_method = mu.PDF_REDACT_IMAGE_NONE
            options.line_art = mu.PDF_REDACT_LINE_ART_NONE
            mu.pdf_redact_page(self._pdf(), pdf_page, options)

    @contextmanager
    def _file_marks_aside(self, page: int) -> Iterator[None]:
        """The page's redaction marks, out of its Annots in the `with`, back in place after."""
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(self._pdf(), page)
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        marks = [
            place
            for place, annotation in enumerate(_items_of(listed))
            if mu.pdf_name_eq(
                mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype), mu.PDF_ENUM_NAME_Redact
            )
        ]
        # No mark of the file's own: nothing to set aside.
        if not marks:
            yield
            return
        # The list as it was, a copy that holds on to each mark while it's out.
        before = mu.pdf_copy_array(listed)
        # From the end, so each deletion leaves the places still to read where they were.
        for place in reversed(marks):
            mu.pdf_array_delete(listed, place)
        self._sync_annotations(page)
        yield
        listed = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)
        _put_back(listed, before, mu.PDF_ENUM_NAME_Redact)
        self._sync_annotations(page)

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
        _put_back(listed, before, mu.PDF_ENUM_NAME_Link)
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
        """Every hidden copy on the page, as text, and each string MuPDF may miss."""
        mu = pymupdf.mupdf
        found: list[str] = []
        for streams, resources in self._drawings(page):
            for marking in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Properties)):
                found += _hidden_copies_in(marking)
            for stream in streams:
                found += self._hidden_copies_written_in(_bytes_of(stream), resources)
        return found

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy on the page; one blanked goes."""
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
        """Each drawing the page and its annotations draw, with the resources it names."""
        mu = pymupdf.mupdf
        page_obj = mu.pdf_lookup_page_obj(self._pdf(), page)
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        # One stream, or a list of them drawn in turn.
        streams = _items_of(contents) if mu.pdf_is_array(contents) else [contents]
        resources = mu.pdf_dict_get_inheritable(page_obj, mu.PDF_ENUM_NAME_Resources)
        waiting = [(streams, resources)] + [
            ([appearance], _resources_of(appearance))
            for appearance in _appearances_of(page_obj)
        ]
        yield from _each_drawing(waiting)

    def _rewrite_written(
        self,
        stream: pymupdf.mupdf.PdfObj,
        resources: pymupdf.mupdf.PdfObj,
        rewritten: Callable[[str], str],
    ) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy written in `stream`."""
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
            # Changed, or a string MuPDF drops: as MuPDF reads it, so no reader finds the other.
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
            # One MuPDF reads in part: a string it drops may be one another reader keeps.
            if self._loses_a_string(marking, written=written):
                found += self._strings_in(written)
            # Those MuPDF reads, a string of its own included.
            found += _hidden_copies_in(marking)
        # From the first only: what follows a later one follows it too.
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
        """Whether MuPDF's reading of a dictionary holds fewer strings than `written` does.

        As with a key written twice: MuPDF keeps the last, another reader the other.
        """
        return len(self._strings_in(_as_written(marking))) < len(self._strings_in(written))

    def _strings_written_in(self, drawing: bytes, resources: pymupdf.mupdf.PdfObj) -> list[str]:
        """Each string `drawing` writes, as text; an image in it is read past.

        After an image MuPDF can't read, every string to the end: nothing says where its
        bytes end. `resources` are those it names an image's colour space from.
        """
        mu = pymupdf.mupdf
        reader = mu.fz_open_buffer(mu.fz_new_buffer_from_copied_data(drawing))
        lexbuf = mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)  # as `_markings_in` has it
        found: list[str] = []
        while True:
            start = mu.fz_tell(reader)
            token = mu.pdf_lex(reader, lexbuf)
            # The end of the drawing.
            if token == mu.PDF_TOK_EOF:
                return found
            # A string: read again from its start, as an object, for its text.
            if token == mu.PDF_TOK_STRING:
                mu.fz_seek(reader, start, 0)
                found.append(_text_of(mu.pdf_parse_stm_obj(self._pdf(), reader, lexbuf)))
                continue
            is_operator = token == mu.PDF_TOK_KEYWORD
            operator = _operator_at(drawing, start, reader) if is_operator else b""
            # Not an image written into the drawing.
            if operator != b"BI":
                continue
            # An image MuPDF can read: its bytes hold no string, and it reads on after them.
            if self._read_past_image(reader, lexbuf, drawing=drawing, resources=resources):
                continue
            # One it can't: what's left may hold anything.
            rest = drawing[start:]
            return found + self._strings_in(rest) + _strings_anywhere_in(rest)

    def _markings_in(
        self, drawing: bytes, resources: pymupdf.mupdf.PdfObj
    ) -> Iterator[tuple[int, int, pymupdf.mupdf.PdfObj | None]]:
        """Each marked content's dictionary in `drawing`, read, with where it starts and ends.

        Read by MuPDF's lexer, its reader of a drawing, so strings and comments read as it draws
        them. Every dictionary counts, whatever operator follows, as MuPDF keeps one even before
        its tag. One not read whole comes as None, as does all after an image MuPDF can't read.
        """
        mu = pymupdf.mupdf
        # No dictionary in it, as in most drawings.
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
            is_operator = token == mu.PDF_TOK_KEYWORD
            operator = _operator_at(drawing, start, reader) if is_operator else b""
            # Not an image written into the drawing.
            if operator != b"BI":
                continue
            # An image MuPDF can read: its bytes aren't drawing, so read on past them.
            if self._read_past_image(reader, lexbuf, drawing=drawing, resources=resources):
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
            dictionary = mu.pdf_parse_dict(self._pdf(), reader, lexbuf)
        except mu.FzErrorSyntax:
            dictionary = None
        parsed_to = mu.fz_tell(reader)
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

        `reader` is just past its BI.
        """
        mu = pymupdf.mupdf
        # MuPDF raises on an image it can't read: its dictionary, colour space or bytes.
        try:
            dictionary = mu.pdf_parse_dict(self._pdf(), reader, lexbuf)
            # A carriage return and line feed after ID are one line end.
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

    def document_hidden_copies(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        """Each of the document's own hidden copies that `holding` is true of, and where."""
        found = [
            HiddenCopy(entry.place, text)
            for entry in self._document_entries()
            for text in self._texts_of(entry, holding)
        ]
        found += [HiddenCopy("metadata", text) for text in self._metadata_texts()]
        return [hidden_copy for hidden_copy in found if holding(hidden_copy.text)]

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(copy)` for the document's hidden copies; one left blank goes."""
        mu = pymupdf.mupdf
        holding = partial(_is_changed_by, rewritten)
        # Read first: a key deleted mid-walk would move the ones after it.
        entries = list(self._document_entries())
        drawings = [entry for entry in entries if entry.writing == "drawing"]
        drawing_a_word = [drawing for drawing in drawings if self._texts_of(drawing, holding)]
        changed = [
            entry
            for entry in entries
            if entry.writing != "drawing" and self._rewrite_entry(entry, rewritten)
        ]
        redrawn = self._redrawn(changed, drawing_a_word)
        for page, on_page in redrawn.items():
            self._draw_again(page, on_page)
        # Only a drawing that drew a word or was drawn again can draw one now: read those again.
        drawn_again = {number for on_page in redrawn.values() for number in on_page}
        for drawing in drawings:
            if drawing in drawing_a_word or mu.pdf_to_num(drawing.holder) in drawn_again:
                self._rewrite_entry(drawing, rewritten)
        self._rewrite_metadata(rewritten)

    def is_signed(self) -> bool:
        """Whether the document holds a signature, or what a signed file keeps for one."""
        mu = pymupdf.mupdf
        root = mu.pdf_dict_get(mu.pdf_trailer(self._pdf()), mu.PDF_ENUM_NAME_Root)
        kept = [mu.pdf_dict_gets(root, key) for key in _SIGNATURE_CHECKS]
        return bool(self._signed_fields(root)) or not all(map(mu.pdf_is_null, kept))

    def drop_signatures(self) -> None:
        """Delete every signature, and what the file keeps only to check one."""
        mu = pymupdf.mupdf
        root = mu.pdf_dict_get(mu.pdf_trailer(self._pdf()), mu.PDF_ENUM_NAME_Root)
        for field in self._signed_fields(root):
            mu.pdf_dict_dels(field, _FIELD_VALUE)
        for key in _SIGNATURE_CHECKS:
            mu.pdf_dict_dels(root, key)

    def _signed_fields(self, root: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
        """Each signed form field: one whose value, a signature, is a dictionary."""
        mu = pymupdf.mupdf
        return [
            field
            for field in self._form_fields(root)
            if mu.pdf_is_dict(mu.pdf_dict_gets(field, _FIELD_VALUE))
        ]

    def _document_entries(self) -> Iterator[_Entry]:
        """Each place the document may keep a hidden copy of its own, and how it's put back."""
        mu = pymupdf.mupdf
        trailer = mu.pdf_trailer(self._pdf())
        root = mu.pdf_dict_get(trailer, mu.PDF_ENUM_NAME_Root)
        info = mu.pdf_dict_get(trailer, mu.PDF_ENUM_NAME_Info)
        for key in map(mu.pdf_to_name, _keys_of(info)):
            # A date says when, not what.
            if key in _INFO_DATES:
                continue
            yield _Entry("title" if key == _INFO_TITLE else "metadata", info, key, "text")
        outlines = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_Outlines)
        for bookmark in _each_reached(
            mu.pdf_dict_get(outlines, mu.PDF_ENUM_NAME_First), _NEXT_BOOKMARKS
        ):
            yield _Entry("bookmarks", bookmark, _BOOKMARK_TITLE, "required_text")
        yield from self._annotation_entries()
        for field in self._form_fields(root):
            yield _Entry("form_fields", field, _FIELD_VALUE, "field_value")
            yield _Entry("form_fields", field, _FIELD_RESET, "text")
            yield _Entry("form_fields", field, _FIELD_FORMATTED, "whole")
            yield _Entry("form_fields", field, _FIELD_OPTIONS, "options")
            yield _Entry("form_fields", field, _FIELD_DESCRIPTION, "text")
            yield _Entry("form_fields", field, _FIELD_SEED_VALUES, "whole")
        # The fields again, as XFA, not edited here: it goes whole once it holds a word.
        form = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_AcroForm)
        yield _Entry("form_fields", form, _FORM_XFA, "whole")
        tags = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_StructTreeRoot)
        for element in _each_reached(mu.pdf_dict_get(tags, mu.PDF_ENUM_NAME_K), _TAG_KIDS):
            for key in _HIDDEN_COPY_KEYS:
                yield _Entry("screen_reader_text", element, key, "text")

    def _annotation_entries(self) -> Iterator[_Entry]:
        """What each annotation draws, on every page, and each comment's words and labels.

        A form field's part (a widget) counts as a form field; its value is read as the field's.
        """
        mu = pymupdf.mupdf
        for page, annotation in self._each_annotation():
            widget = _is_widget(annotation)
            drawn_from_words = widget or _is_free_text(annotation)
            shown = _Shown(page, mu.pdf_to_num(annotation)) if drawn_from_words else None
            place: CopyPlace = "form_fields" if widget else "comments"
            yield _Entry(place, annotation, _APPEARANCE, "drawing", shown=shown)
            # A form field's part: its captions and its description of itself.
            if widget:
                captions = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_MK)
                for key in _CAPTIONS:
                    yield _Entry("form_fields", captions, key, "text", shown=shown)
                yield _Entry("form_fields", annotation, _ANNOTATION_WORDS, "text")
                continue
            yield _Entry("comments", annotation, _ANNOTATION_WORDS, "text", shown=shown)
            for key in _COMMENT_LABELS:
                yield _Entry("comments", annotation, key, "text", shown=shown)
            yield _Entry("comments", annotation, _FORMATTED_WORDS, "whole", shown=shown)

    def _form_fields(self, root: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
        """Each form field once: those the form lists, then those a page shows that it doesn't.

        A broken or half-flattened form can leave off a field that still shows on the page.
        """
        mu = pymupdf.mupdf
        form = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_AcroForm)
        fields = list(
            _each_reached(mu.pdf_dict_get(form, mu.PDF_ENUM_NAME_Fields), _FIELD_KIDS)
        )
        reached = {mu.pdf_to_num(field) for field in fields}
        for _page, annotation in self._each_annotation():
            # A comment or a link, not a form field's part.
            if not _is_widget(annotation):
                continue
            for field in _field_line(annotation):
                # Reached already, through the form's list or another of its parts.
                if mu.pdf_to_num(field) in reached:
                    continue
                reached.add(mu.pdf_to_num(field))
                fields.append(field)
        return fields

    def _each_annotation(self) -> Iterator[tuple[int, pymupdf.mupdf.PdfObj]]:
        """Each annotation on every page, with its page: comments, links, form fields' parts."""
        mu = pymupdf.mupdf
        pdf = self._pdf()
        for page in range(mu.pdf_count_pages(pdf)):
            page_obj = mu.pdf_lookup_page_obj(pdf, page)
            for annotation in _items_of(mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Annots)):
                yield page, annotation

    def _texts_of(self, entry: _Entry, holding: Callable[[str], bool]) -> list[str]:
        """The text `entry` holds: its key's, or the words of its options or drawing."""
        writing = entry.writing
        value = pymupdf.mupdf.pdf_dict_gets(entry.holder, entry.key)
        # Text, or a list of it.
        if writing == "text":
            return _texts_in(value)
        # Text the file must have.
        if writing == "required_text":
            return _texts_in(value)
        # Words that go whole once changed, wherever in it they are.
        if writing == "whole":
            return _texts_within(value)
        # A form field's value: text, or a list of it.
        if writing == "field_value":
            return _texts_in(value)
        # A choice field's options: a list of them.
        if writing == "options":
            return [words for option in _items_of(value) for words in _option_words(option)]
        # An annotation's drawing: the strings it writes.
        if writing == "drawing":
            return self._strings_drawn_by(entry.holder, holding)
        assert_never(writing)

    def _rewrite_entry(self, entry: _Entry, rewritten: Callable[[str], str]) -> bool:
        """Put `rewritten(copy)` for the copy `entry` holds, as it's put; whether it changed."""
        mu = pymupdf.mupdf
        writing = entry.writing
        # A choice field's options: a list, each option rewritten in it.
        if writing == "options":
            return self._rewrite_options(entry.holder, rewritten)
        # As it was: it stays as the file wrote it.
        if not _changes(self._texts_of(entry, partial(_is_changed_by, rewritten)), rewritten):
            return False
        # Words kept in a stream, which can't be written back in place: gone whole.
        if mu.pdf_is_stream(mu.pdf_dict_gets(entry.holder, entry.key)):
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # Words that go whole once changed: gone.
        if writing == "whole":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # A drawing that still draws a word: gone, so nothing shows it.
        if writing == "drawing":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # Plain words, or a list of them: words of their own left stay, else they go.
        if writing == "text":
            self._put_words(entry.holder, entry.key, rewritten)
            return True
        # Text the file must have: what's left, empty if nothing is.
        if writing == "required_text":
            kept = rewritten(mu.pdf_to_text_string(mu.pdf_dict_gets(entry.holder, entry.key)))
            mu.pdf_dict_puts(entry.holder, entry.key, mu.pdf_new_text_string(kept))
            return True
        # A form field's value: set as the form sets it, so the field shows it, even empty.
        if writing == "field_value":
            self._set_field_value(entry.holder, rewritten)
            return True
        assert_never(writing)

    def _put_words(
        self, holder: pymupdf.mupdf.PdfObj, key: str, rewritten: Callable[[str], str]
    ) -> None:
        """Put `rewritten(words)` for the text under `key`, or for each text in its list.

        A text left blank goes.
        """
        mu = pymupdf.mupdf
        value = mu.pdf_dict_gets(holder, key)
        # A list of text, as a multi-select list box's value.
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(holder, key, self._rewritten_list(value, rewritten))
            return
        _put_text(holder, key, rewritten(mu.pdf_to_text_string(value)))

    def _set_field_value(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> None:
        """Set a form field's value to `rewritten(value)`, as the form sets it.

        Its chosen options (I) go: they name places in a list that may have changed.
        """
        mu = pymupdf.mupdf
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        value = mu.pdf_dict_gets(field, _FIELD_VALUE)
        # A list of values: MuPDF sets only one.
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(field, _FIELD_VALUE, self._rewritten_list(value, rewritten))
            return
        kept = rewritten(mu.pdf_to_text_string(value))
        mu.pdf_set_field_value(self._pdf(), field, kept, 1)  # 1: run no script

    def _rewritten_list(
        self, written: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> pymupdf.mupdf.PdfObj:
        """A list with `rewritten(words)` for each text in it; a text left blank goes."""
        mu = pymupdf.mupdf
        items = _items_of(written)
        kept = mu.pdf_new_array(self._pdf(), len(items))
        for item in items:
            # Not text: it stays.
            if not mu.pdf_is_string(item):
                mu.pdf_array_push(kept, item)
                continue
            words = rewritten(mu.pdf_to_text_string(item))
            # Left blank: a blank line in a list says nothing.
            if not words:
                continue
            mu.pdf_array_push(kept, mu.pdf_new_text_string(words))
        return kept

    def _rewrite_options(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> bool:
        """Put `rewritten(words)` in each of a choice field's options; whether any changed.

        An option left showing nothing goes, and with any change so do the chosen
        options (I), named by their places in the list.
        """
        mu = pymupdf.mupdf
        written = [_option_words(option) for option in _items_of(_options_of(field))]
        kept = [[rewritten(words) for words in option] for option in written]
        # As they were: they stay as the file wrote them.
        if kept == written:
            return False
        options = mu.pdf_new_array(self._pdf(), len(kept))
        for option in kept:
            # Showing nothing, or no words at all: it goes.
            if not option or not option[-1]:
                continue
            mu.pdf_array_push(options, _new_option(self._pdf(), option))
        mu.pdf_dict_put(field, mu.PDF_ENUM_NAME_Opt, options)
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        return True

    def _redrawn(
        self, changed: list[_Entry], drawing_a_word: list[_Entry]
    ) -> dict[int, set[int]]:
        """Annotations to redraw, by page: those drawn from words, changed or drawing a word."""
        mu = pymupdf.mupdf
        fields = {
            mu.pdf_to_num(entry.holder) for entry in changed if entry.writing in _FIELD_SHOWN
        }
        redrawn = self._fields_shown(fields)
        for entry in [*changed, *drawing_a_word]:
            # One MuPDF draws from its words, which is shown.
            if entry.shown is not None:
                redrawn.setdefault(entry.shown.page, set()).add(entry.shown.annotation)
        return redrawn

    def _fields_shown(self, fields: set[int]) -> dict[int, set[int]]:
        """Where each of `fields` shows: the page, and the field's own parts there."""
        mu = pymupdf.mupdf
        shown: dict[int, set[int]] = {}
        # No field changed: no page to read.
        if not fields:
            return shown
        for page, annotation in self._each_annotation():
            if fields & {mu.pdf_to_num(field) for field in _field_line(annotation)}:
                shown.setdefault(page, set()).add(mu.pdf_to_num(annotation))
        return shown

    def _draw_again(self, page: int, annotations: set[int]) -> None:
        """Have MuPDF draw these annotations on `page` again, from their words.

        One it can't draw keeps its old drawing, which is read again after.
        """
        mu = pymupdf.mupdf
        pdf_page = mu.pdf_page_from_fz_page(self.doc[page].this)
        for annotation in _annotations_on(pdf_page):
            if mu.pdf_to_num(mu.pdf_annot_obj(annotation)) in annotations:
                mu.pdf_dirty_annot(annotation)
        mu.pdf_update_page(pdf_page)

    def _strings_drawn_by(
        self, annotation: pymupdf.mupdf.PdfObj, holding: Callable[[str], bool]
    ) -> list[str]:
        """Each string an annotation's drawing writes, and the drawings it draws, as text.

        Read as written, not as drawn: another viewer may draw what MuPDF won't.
        """
        appearances = [
            ([appearance], _resources_of(appearance))
            for appearance in _appearance_streams(annotation)
        ]
        drawings = [
            (_bytes_of(stream), resources)
            for streams, resources in _each_drawing(appearances)
            for stream in streams
        ]
        return [
            text
            for drawing, resources in drawings
            if _may_hold(drawing, holding)
            for text in self._strings_written_in(drawing, resources)
        ]

    def _metadata_texts(self) -> list[str]:
        """The text in the document's metadata (XMP), each string apart; whole if not XML."""
        stream = self._metadata_stream()
        # No metadata stream.
        if stream is None:
            return []
        written = _bytes_of(stream)
        dom = _xml_of(written)
        # Not XML: any of it may hold a word, in any encoding it may be in.
        if dom is None:
            return [_readings_of(written)]
        return [str(piece.nodeValue) for piece in _xml_strings(dom)]

    def _rewrite_metadata(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(text)` for each string in the metadata (XMP); it goes if not XML."""
        mu = pymupdf.mupdf
        stream = self._metadata_stream()
        # No metadata stream.
        if stream is None:
            return
        written = _bytes_of(stream)
        dom = _xml_of(written)
        # Not XML, so not written back: it goes, once anything in it would change.
        if dom is None:
            if _changes([_readings_of(written)], rewritten):
                root = mu.pdf_dict_get(mu.pdf_trailer(self._pdf()), mu.PDF_ENUM_NAME_Root)
                mu.pdf_dict_del(root, mu.PDF_ENUM_NAME_Metadata)
            return
        changed = False
        for piece in _xml_strings(dom):
            text = str(piece.nodeValue)
            kept = rewritten(text)
            if kept != text:
                piece.nodeValue = kept
                changed = True
        # Nothing changed: it stays as the file wrote it.
        if not changed:
            return
        # Without an XML declaration, as metadata is written: UTF-8 needs none.
        new_xml = "".join(node.toxml() for node in dom.childNodes).encode()
        mu.pdf_update_stream(self._pdf(), stream, mu.fz_new_buffer_from_copied_data(new_xml), 0)

    def _metadata_stream(self) -> pymupdf.mupdf.PdfObj | None:
        """The document's metadata (XMP), a stream the catalog names; None when it has none."""
        mu = pymupdf.mupdf
        root = mu.pdf_dict_get(mu.pdf_trailer(self._pdf()), mu.PDF_ENUM_NAME_Root)
        stream = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_Metadata)
        return stream if mu.pdf_is_stream(stream) else None

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

    def add_drawing(self, page: int, drawing: bytes) -> None:
        """Add `drawing` last in the page's list of drawings (its Contents), so it's on top.

        Added to the page's own list, not by writing the list out again: each edit adds one.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        page_obj = mu.pdf_lookup_page_obj(pdf, page)
        added = mu.pdf_add_stream(
            pdf, mu.fz_new_buffer_from_copied_data(drawing), mu.PdfObj(), 0
        )
        contents = mu.pdf_dict_get(page_obj, mu.PDF_ENUM_NAME_Contents)
        # A list of the page's own, written in place: the drawing goes last.
        if mu.pdf_is_array(contents) and not mu.pdf_is_indirect(contents):
            mu.pdf_array_push(contents, added)
            return
        listed = _own_list_of(pdf, contents)
        mu.pdf_array_push(listed, added)
        mu.pdf_dict_put(page_obj, mu.PDF_ENUM_NAME_Contents, listed)

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page, read unturned, into the PDF's own coordinates.

        Not page.transformation_matrix: on a turned page it forgets where the
        page's box starts.
        """
        moved = pymupdf.Point(*point) * self._to_pdf_matrix(page)
        return (moved.x, moved.y)

    def _to_pdf_matrix(self, page: int) -> pymupdf.Matrix:
        """What turns a point on the page, read unturned, into the PDF's own coordinates."""
        mu = pymupdf.mupdf
        with self._unturned(page) as pdf_page:
            _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
            mu.pdf_page_transform(pdf_page, _mediabox, page_to_screen)
        return ~pymupdf.Matrix(page_to_screen)

    @contextmanager
    def _unturned(self, page: int) -> Iterator[pymupdf.mupdf.PdfPage]:
        """The page with its turn (its Rotate) taken off inside the `with`, put back after.

        Not PyMuPDF's rotation_matrix: it turns from the whole crop box, MuPDF from the part on
        the paper (the MediaBox), so they differ on a page cropped past its paper.
        """
        pg = self.doc[page]
        turn_cw = pg.rotation
        # A page not turned is left as it is, with no Rotate written.
        if turn_cw:
            pg.set_rotation(0)
        try:
            yield pymupdf.mupdf.pdf_page_from_fz_page(pg.this)
        finally:
            if turn_cw:
                pg.set_rotation(turn_cw)

    def _sync_links(self, page: int) -> None:
        """Have MuPDF read the page's links again: it keeps a list of its own, read once."""
        pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(self.doc[page].this)
        pymupdf.mupdf.pdf_sync_links(pdf_page)

    def _sync_annotations(self, page: int) -> None:
        """Have MuPDF read the page's annotations again: it keeps its own list, read once."""
        pdf_page = pymupdf.mupdf.pdf_page_from_fz_page(self.doc[page].this)
        pymupdf.mupdf.pdf_sync_annots(pdf_page)

    def _pdf(self) -> pymupdf.mupdf.PdfDocument:
        """The same document, as MuPDF's low-level API needs it."""
        return pymupdf.mupdf.pdf_document_from_fz_document(self.doc.this)


def _put_back(
    listed: pymupdf.mupdf.PdfObj, before: pymupdf.mupdf.PdfObj, kind: pymupdf.mupdf.PdfObj
) -> None:
    """Put each annotation of `kind` that `before` lists and `listed` lost back in its place.

    Only deletions happened since `before`, so what's left keeps its order.
    """
    mu = pymupdf.mupdf
    place = 0  # where the next one goes back
    for annotation in _items_of(before):
        # Still listed: the next one goes after it.
        if mu.pdf_array_find(listed, annotation) >= 0:
            place += 1
            continue
        subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
        # Another kind of annotation: it stays gone.
        if not mu.pdf_name_eq(subtype, kind):
            continue
        mu.pdf_array_insert(listed, annotation, place)
        place += 1


def _own_list_of(
    pdf: pymupdf.mupdf.PdfDocument, contents: pymupdf.mupdf.PdfObj
) -> pymupdf.mupdf.PdfObj:
    """A new list, written in place, of the drawings a page's Contents names."""
    mu = pymupdf.mupdf
    # A list kept apart, which another page may share: a copy, the page's alone.
    if mu.pdf_is_array(contents):
        return mu.pdf_copy_array(contents)
    listed = mu.pdf_new_array(pdf, 1)
    # One drawing, listed first; an empty m_internal means none.
    if contents.m_internal:
        mu.pdf_array_push(listed, contents)
    return listed


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


def _letter_of(font: pymupdf.mupdf.pdf_font_desc, code: int, cid: int) -> str | None:
    """The letter a code is, by the font's letter list, else by MuPDF's from its encoding.

    None when neither says, or the code is several letters (like "fi").
    """
    mu = pymupdf.mupdf
    # Its letter list (ToUnicode), when it has one.
    if font.to_unicode is not None:
        codepoint = mu.ll_pdf_lookup_cmap(font.to_unicode, code)
        return chr(codepoint) if 0 <= codepoint <= sys.maxunicode else None
    # MuPDF's own, worked out from the encoding's letter names: a C array, read in place.
    if cid >= font.cid_to_ucs_len:
        return None
    codepoint = ctypes.cast(int(font.cid_to_ucs), ctypes.POINTER(ctypes.c_ushort))[cid]
    return chr(codepoint) if codepoint else None


def _cids_in(w: str) -> list[int] | None:
    """Every CID a two-byte font's `/W` gives a width: `c [w1 w2 ...]` or `first last w`.

    None when it doesn't read as either. A run past the codes a font can have stops there.
    """
    tokens = _W_TOKEN.findall(w)[1:-1]  # inside the list's own brackets
    cids: set[int] = set()
    place = 0
    try:
        while place < len(tokens):
            first = max(int(float(tokens[place])), 0)
            following = tokens[place + 1]
            # `c [w1 w2 ...]`: one CID per width, from c on.
            if following == "[":
                closing = tokens.index("]", place + 2)
                last = first + closing - place - 3
                place = closing + 1
            # `first last w`: every CID from first to last, all one width.
            else:
                last = int(float(following))
                place += _RUN_TOKENS
            cids.update(range(first, min(last, _TWO_BYTE_CODES - 1) + 1))
    except IndexError, ValueError:  # raised by a list cut short or a word where a number goes
        return None
    return sorted(cids)


def _whole_number(value: str | None) -> int | None:
    """An entry that's a whole number, as one; None for anything else, or nothing."""
    if value is None or not _WHOLE_NUMBER.fullmatch(value):
        return None
    return int(value)


def _values_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each value in a PDF dictionary; none when it isn't one."""
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_val(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def _appearances_of(page: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each appearance stream of each annotation on the page: plain, hovered and pressed."""
    mu = pymupdf.mupdf
    for annotation in _items_of(mu.pdf_dict_get(page, mu.PDF_ENUM_NAME_Annots)):
        yield from _appearance_streams(annotation)


def _appearance_streams(annotation: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each appearance of an annotation: the drawing it shows, in each state."""
    mu = pymupdf.mupdf
    for appearance in _values_of(mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_AP)):
        # One stream, or one for each state, as a checkbox's on and off.
        yield from [appearance] if mu.pdf_is_stream(appearance) else _values_of(appearance)


def _each_drawing(
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


def _operator_at(drawing: bytes, start: int, reader: pymupdf.mupdf.FzStream) -> bytes:
    """The operator just read from `drawing`: the last word read, past any space or comment."""
    return drawing[start : pymupdf.mupdf.fz_tell(reader)].split()[-1]


# A drawing's stream, and the resources it names things from.
type _Drawing = tuple[pymupdf.mupdf.PdfObj, pymupdf.mupdf.PdfObj]


def _drawn_by(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each drawing `resources` names, each kept in a stream of its own."""
    yield from _forms_in(resources)
    yield from _tiling_patterns_in(resources)
    yield from _soft_masks_in(resources)
    yield from _type3_letters_in(resources)


def _forms_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each form (XObject) `resources` names."""
    mu = pymupdf.mupdf
    for xobject in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_XObject)):
        subtype = mu.pdf_dict_get(xobject, mu.PDF_ENUM_NAME_Subtype)
        # A form: an image holds no drawing.
        if mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Form):
            yield xobject, _resources_of(xobject)


def _tiling_patterns_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """Each tiling pattern `resources` names."""
    mu = pymupdf.mupdf
    for pattern in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_Pattern)):
        # A tiling pattern: a shading one is a plain dictionary.
        if mu.pdf_is_stream(pattern):
            yield pattern, _resources_of(pattern)


def _soft_masks_in(resources: pymupdf.mupdf.PdfObj) -> Iterator[_Drawing]:
    """The drawing of each soft mask the graphics states in `resources` name."""
    mu = pymupdf.mupdf
    for state in _values_of(mu.pdf_dict_get(resources, mu.PDF_ENUM_NAME_ExtGState)):
        mask = mu.pdf_dict_get(state, mu.PDF_ENUM_NAME_SMask)
        group = mu.pdf_dict_get(mask, mu.PDF_ENUM_NAME_G)
        # A soft mask, not absent or /None.
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
    # A copy: a changed stream lends its own buffer; taking its bytes would empty the stream.
    return mu.fz_buffer_extract_copy(mu.pdf_load_stream(stream))


def _read_past_dictionary(
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


def _load_inline_image(
    pdf: pymupdf.mupdf.PdfDocument,
    dictionary: pymupdf.mupdf.PdfObj,
    *,
    reader: pymupdf.mupdf.FzStream,
    resources: pymupdf.mupdf.PdfObj,
) -> None:
    """Read an image's bytes from `reader`, as MuPDF does drawing it."""
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

    MuPDF's own reading stops at a NUL, except in UTF-16; a space keeps the words apart.
    """
    mu = pymupdf.mupdf
    # A string that's an object of the file would be written as its reference.
    string = mu.pdf_resolve_indirect(string)
    written = _as_written(string)
    # Written as text, it holds no NUL: MuPDF writes an unprintable string as hex.
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
    # Parsed from hex: MuPDF's own maker of a string takes text, not bytes.
    written = mu.fz_new_buffer_from_copied_data(b"<" + held.hex().encode() + b">")
    lexbuf = mu.PdfLexbuf(mu.PDF_LEXBUF_SMALL)  # as `_markings_in` has it
    # A string belongs to no file, so it's read without one.
    return mu.pdf_parse_stm_obj(mu.PdfDocument(), mu.fz_open_buffer(written), lexbuf)


def _strings_anywhere_in(written: bytes) -> list[str]:
    """Each string in `written`, part of a drawing, as text, wherever a reader may start one.

    That's at any ( or <, since a reader may read the bytes before it otherwise than MuPDF.
    """
    held = _literal_strings_in(written) + _hex_strings_in(written)
    return [text for string in held for text in _texts_of(string)]


def _literal_strings_in(written: bytes) -> list[bytes]:
    """The bytes of each string in brackets in `written`, through the ) that closes it.

    One inside another is read only as part of it, since it ends inside it.
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
    """The bytes of each string in hex in `written`, whole and from each < to the next.

    Each from its first digit and its second too: a reader starting partway lines up with one.
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
    """The text of a string holding `held`, and again in each encoding a mark in it names.

    A string may start at a mark inside it, so `held` is read from either byte a UTF-16
    letter may start on, whole and cut at each ), where such a string ends.
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
    texts = (_text_at(marking, key) for key in _HIDDEN_COPY_KEYS)
    return [text for text in texts if text is not None]


def _rewrite_hidden_copies_in(
    marking: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
) -> bool:
    """Put `rewritten(hidden_copy)` for each hidden copy in `marking`; whether any changed."""
    changed = False
    for key in _HIDDEN_COPY_KEYS:
        hidden_copy = _text_at(marking, key)
        # Not there, or not text.
        if hidden_copy is None:
            continue
        kept = rewritten(hidden_copy)
        # As it was: it stays as the file wrote it.
        if kept == hidden_copy:
            continue
        changed = True
        _put_text(marking, key, kept)
    return changed


def _text_at(holder: pymupdf.mupdf.PdfObj, key: str) -> str | None:
    """The text a dictionary holds under `key`; None when it holds none there, or no text."""
    mu = pymupdf.mupdf
    value = mu.pdf_dict_gets(holder, key)
    return _text_of(value) if mu.pdf_is_string(value) else None


def _put_text(holder: pymupdf.mupdf.PdfObj, key: str, text: str) -> None:
    """Put `text` in a dictionary under `key`: words left stay, and with none the key goes."""
    mu = pymupdf.mupdf
    # Words of its own left: they stay.
    if text:
        mu.pdf_dict_puts(holder, key, mu.pdf_new_text_string(text))
        return
    # Nothing left: the key goes.
    mu.pdf_dict_dels(holder, key)


def _keys_of(dictionary: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """Each key in a PDF dictionary, as a name; none when it isn't one."""
    mu = pymupdf.mupdf
    return [
        mu.pdf_dict_get_key(dictionary, place) for place in range(mu.pdf_dict_len(dictionary))
    ]


def _each_reached(
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
            waiting += reversed(_items_of(node))
            continue
        # Anything else that isn't a dictionary (nothing there, a number) leads nowhere.
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += reversed([mu.pdf_dict_gets(node, key) for key in keys])


def _texts_in(value: pymupdf.mupdf.PdfObj) -> list[str]:
    """The text a value holds: a string's, a stream's read whole, or each in a list of them."""
    mu = pymupdf.mupdf
    items = _items_of(value) if mu.pdf_is_array(value) else [value]
    return [
        _held_text_of(item)
        for item in items
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item)
    ]


def _texts_within(value: pymupdf.mupdf.PdfObj) -> list[str]:
    """The text a value holds anywhere in it: in it, and in its lists and dictionaries.

    Each object once: a file's tree can lead back to where it was.
    """
    mu = pymupdf.mupdf
    texts: list[str] = []
    waiting = [value]
    seen: set[int] = set()  # objects of their own reached already, by number
    while waiting:
        item = waiting.pop()
        number = mu.pdf_to_num(item)
        # An object of its own, reached already.
        if mu.pdf_is_indirect(item) and number in seen:
            continue
        seen.add(number)
        # Text: a string, or a stream read whole.
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item):
            texts.append(_held_text_of(item))
            continue
        # A list or a dictionary: each of its items is reached, in order.
        if mu.pdf_is_array(item):
            waiting += reversed(_items_of(item))
            continue
        waiting += reversed(_values_of(item))
    return texts


def _held_text_of(item: pymupdf.mupdf.PdfObj) -> str:
    """A string's text, all of it, or a stream's read whole, in each encoding it may be in."""
    mu = pymupdf.mupdf
    # A string: PDF says how it's encoded.
    if mu.pdf_is_string(item):
        return _text_of(item)
    return _readings_of(_bytes_of(item))


def _readings_of(written: bytes) -> str:
    """Bytes in an encoding they don't name, read in each XMP may use, one after another."""
    return "\n".join(written.decode(encoding, "replace") for encoding in _XMP_ENCODINGS)


def _changes(texts: list[str], rewritten: Callable[[str], str]) -> bool:
    """Whether `rewritten` changes any of `texts`."""
    return any(_is_changed_by(rewritten, text) for text in texts)


def _is_changed_by(rewritten: Callable[[str], str], text: str) -> bool:
    """Whether `rewritten` changes `text`: whether it holds a word `rewritten` deletes."""
    return rewritten(text) != text


def _may_hold(drawing: bytes, holding: Callable[[str], bool]) -> bool:
    """Whether a string in `drawing` may hold a word `holding` looks for.

    A plain drawing `holding` is false of, read whole, holds none: a string's
    brackets set its words apart.
    """
    # Not plain: a string in it may read other than it's written.
    if _PLAIN_DRAWING.fullmatch(drawing) is None:
        return True
    return holding(drawing.decode("ascii"))


def _options_of(field: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    """A choice field's options, a list; nothing there for any other field."""
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(field, mu.PDF_ENUM_NAME_Opt)


def _option_words(option: pymupdf.mupdf.PdfObj) -> list[str]:
    """An option's words: its own, or a pair's, what it saves and then what it shows."""
    mu = pymupdf.mupdf
    parts = _items_of(option) if mu.pdf_is_array(option) else [option]
    return [mu.pdf_to_text_string(part) for part in parts if mu.pdf_is_string(part)]


def _new_option(pdf: pymupdf.mupdf.PdfDocument, option: list[str]) -> pymupdf.mupdf.PdfObj:
    """An option for a choice field: its words alone, or a pair of them, as it came."""
    mu = pymupdf.mupdf
    # Its words alone.
    if len(option) == 1:
        return mu.pdf_new_text_string(option[0])
    pair = mu.pdf_new_array(pdf, len(option))
    for words in option:
        mu.pdf_array_push(pair, mu.pdf_new_text_string(words))
    return pair


def _is_widget(annotation: pymupdf.mupdf.PdfObj) -> bool:
    """Whether an annotation is a form field's part on the page (a widget)."""
    mu = pymupdf.mupdf
    subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
    return bool(mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_Widget))


def _is_free_text(annotation: pymupdf.mupdf.PdfObj) -> bool:
    """Whether an annotation is a comment written on the page (a FreeText)."""
    mu = pymupdf.mupdf
    subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
    return bool(mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_FreeText))


def _field_line(annotation: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
    """An annotation, and the fields above it through each one's Parent, from it up."""
    mu = pymupdf.mupdf
    line: list[pymupdf.mupdf.PdfObj] = []
    numbers: set[int] = set()  # those in the line, by their numbers
    node = annotation
    # Up to the top, once each: a broken file's parents can go round.
    while mu.pdf_is_dict(node) and mu.pdf_to_num(node) not in numbers:
        line.append(node)
        numbers.add(mu.pdf_to_num(node))
        node = mu.pdf_dict_get(node, mu.PDF_ENUM_NAME_Parent)
    return line


def _annotations_on(pdf_page: pymupdf.mupdf.PdfPage) -> Iterator[pymupdf.mupdf.PdfAnnot]:
    """Each annotation MuPDF has loaded on a page: comments, links, then form fields."""
    mu = pymupdf.mupdf
    annotation = mu.pdf_first_annot(pdf_page)
    while annotation.m_internal:
        yield annotation
        annotation = mu.pdf_next_annot(annotation)
    widget = mu.pdf_first_widget(pdf_page)
    while widget.m_internal:
        yield widget
        widget = mu.pdf_next_widget(widget)


def _xml_of(written: bytes) -> minidom.Document | None:
    """XML as written, read; None when it can't be read, declares words, or nests too deep.

    A DOCTYPE is written back as it came, with any words it declares (an entity), so
    XML with one goes whole.
    """
    # Not XML, an unknown encoding, or one expat can't read, as UTF-32, which XMP allows.
    try:
        dom = minidom.parseString(written)
    except ExpatError, LookupError, ValueError:
        return None
    # A DOCTYPE: what it declares isn't read.
    if dom.doctype is not None:
        return None
    # Nested too deep to read through.
    if _nests_deeper(dom, _XML_DEEPEST):
        return None
    return dom


def _nests_deeper(dom: minidom.Document, levels: int) -> bool:
    """Whether XML nests more than `levels` deep, read a level at a time, not by calls."""
    level: list[minidom.Node] = list(dom.childNodes)
    for _deeper in range(levels):
        level = [child for node in level for child in node.childNodes]
    return bool(level)


def _xml_strings(node: minidom.Node) -> Iterator[minidom.Node]:
    """Each piece of text in XML: its text, comments, notes and attributes' values, as nodes.

    Not an attribute XML or RDF reads, the packet's wrapper, or a typed value:
    rewriting one would break the metadata.
    """
    for child in node.childNodes:
        # The packet's wrapper (xpacket), which says where the metadata starts and ends.
        if isinstance(child, minidom.ProcessingInstruction) and child.target == _XMP_PACKET:
            continue
        # Text (CDATA too), a comment, or a note to a program (a processing instruction).
        if isinstance(child, minidom.CharacterData | minidom.ProcessingInstruction):
            yield child
        # A typed value, which says when, which or what kind, not what.
        if isinstance(child, minidom.Element) and _is_typed(child):
            continue
        # An element: its attributes' values, then what's inside.
        if isinstance(child, minidom.Element):
            values = child.attributes.values()
            yield from (value for value in values if _is_xml_text(value))
            yield from _xml_strings(child)


def _is_typed(node: minidom.Element | minidom.Attr) -> bool:
    """Whether an XML element or attribute is a typed metadata value, by its name."""
    # .get: most namespaces name no typed value.
    return node.localName in _XMP_TYPED.get(node.namespaceURI or "", ())


def _is_xml_text(attribute: minidom.Attr) -> bool:
    """Whether an attribute holds text: not one XML or RDF reads, nor a typed value."""
    return attribute.namespaceURI not in _XML_OWN and not _is_typed(attribute)


@dataclass(frozen=True, slots=True)
class _Shown:
    """An annotation MuPDF draws from its words, a widget or a FreeText: its page and number."""

    page: int
    annotation: int


@dataclass(frozen=True, slots=True, eq=False)
class _Entry:
    """A string the document may keep a hidden copy in: where, the dictionary, its key.

    `writing` is how a new copy goes back; `shown`, the annotation to redraw once it changes.
    """

    place: CopyPlace
    holder: pymupdf.mupdf.PdfObj
    key: str
    writing: _Writing
    shown: _Shown | None = None
