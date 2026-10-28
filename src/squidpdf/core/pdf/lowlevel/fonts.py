"""The fonts the file keeps, through MuPDF's own records: their codes, letters and widths."""

from __future__ import annotations

import ctypes
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.app.message import Message
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.pdf.lowlevel.errors import MUPDF_ERRORS
from squidpdf.core.pdf.lowlevel.objects import PDF_NULL, pdf_of
from squidpdf.core.types import FontCode, GlyphId

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255

# A number, or a bracket, in a two-byte font's width list (`/W`).
_W_TOKEN = re.compile(r"\[|\]|-?\d+(?:\.\d+)?")

_RUN_TOKENS = 3  # `first last w`: a run of CIDs all one width

_TWO_BYTE_CODES = 65536  # a two-byte font has codes 0-65535

_WHOLE_NUMBER = re.compile(r"-?\d+")

_IDENTITY_ENCODINGS = ("/Identity-H", "/Identity-V")  # a two-byte font whose codes are its CIDs

# An object reference, `12 0 R`: the number is what's kept.
_OBJECT_REFERENCE = re.compile(r"(\d+) \d+ R")

# The object the first entry of an array points at: "[15 0 R]" -> 15.
_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")


@dataclass(frozen=True, slots=True, eq=False)
class FontRecords:
    """The fonts the file keeps, as MuPDF's own records of them read."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def font_codes(self, font_object: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first.

        None if the font has no letter list (its ToUnicode) or MuPDF can't read
        that list; DriverError if MuPDF can't load the font at all. `code_bytes`
        is 1 for a simple font, 2 for a Type0 (two-byte) font.
        """
        value_type, _value = self.doc.xref_get_key(font_object, "ToUnicode")
        if value_type == PDF_NULL:
            return None

        with self._mupdf_font_record(font_object) as font:
            if font.to_unicode is None:  # MuPDF couldn't read it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Type0: one code per glyph, so stop after the last glyph
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (_font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]

    def listed_widths(self, font_object: int) -> dict[str, float] | None:
        """Each letter the font's width list gives a width, per 1000 em, lowest code first.

        None when it has no list: `/Widths` for a simple font, `/W` on a two-byte font's
        one inside. A width of 0 is a code the file never draws, and is left out.
        """
        listed = self._listed_codes(font_object)
        if listed is None:
            return None
        widths: dict[str, float] = {}
        with self._mupdf_font_record(font_object) as font:
            for code in listed:
                cid = pymupdf.mupdf.ll_pdf_lookup_cmap(font.encoding, code)
                letter = _letter_of(font, code, cid)
                width = pymupdf.mupdf.ll_pdf_lookup_hmtx(font, cid).w
                if letter is not None and width > 0:
                    widths.setdefault(letter, width)  # lowest code first: the one a page writes
        return widths

    def _listed_codes(self, font_object: int) -> range | list[int] | None:
        """The codes the font's width list gives widths for; None without one that reads."""
        # A simple font: its list runs from FirstChar to LastChar, within its byte.
        if self._entry(font_object, "Widths") is not None:
            first = _whole_number(self._entry(font_object, "FirstChar"))
            last = _whole_number(self._entry(font_object, "LastChar"))
            if first is None or last is None:
                return None
            return range(max(first, 0), min(last, _ONE_BYTE_CODES - 1) + 1)
        value_type, _value = self.doc.xref_get_key(font_object, "DescendantFonts")
        # Neither kind of list: the reader falls back on widths of its own.
        if value_type == PDF_NULL:
            return None
        # `/W` lists CIDs: only by Identity is a code its CID, so its letter can be read.
        if self._entry(font_object, "Encoding") not in _IDENTITY_ENCODINGS:
            return None
        inner = self.inner_font(font_object)
        # The one inside written in place, or nowhere in the file: not read.
        if inner is None:
            return None
        w = self._entry(inner, "W")
        return None if w is None else _cids_in(w)

    def inner_font(self, font_object: int) -> int | None:
        """A two-byte (Type0) font's inner font, by number; None without one of its own.

        None too when it, or the list it's in, points past the file's last object.
        """
        value_type, value = self.doc.xref_get_key(font_object, "DescendantFonts")
        # The list kept as an object of its own ("12 0 R"): read it there.
        if value_type == "xref":
            listed = int(value.split()[0])
            # A damaged file can point past its last object: there's no list to read.
            if not self._has_object(listed):
                return None
            value = self.doc.xref_object(listed, compressed=True)
        inner = _FIRST_REFERENCE.match(value)
        # No list, as a simple font has, or its inner font written out in place: rare.
        if inner is None:
            return None
        number = int(inner.group(1))
        return number if self._has_object(number) else None

    def _has_object(self, xref: int) -> bool:
        """Whether the file has an object numbered `xref`: none is 0, or past its last."""
        return 0 < xref < self.doc.xref_length()

    def _entry(self, xref: int, key: str) -> str | None:
        """An object's entry as written, a reference to another followed; None when absent."""
        value_type, value = self.doc.xref_get_key(xref, key)
        if value_type == PDF_NULL:
            return None
        # Kept in an object of its own, `9 0 R`: what that object holds.
        referenced = _OBJECT_REFERENCE.search(value) if value_type == "xref" else None
        if referenced is not None:
            return self.doc.xref_object(int(referenced.group(1)), compressed=True)
        return value

    def text_font_name(self, font_object: int) -> str | None:
        """The name text in font `font_object` is read under, often the font file's own.

        A page can list a font as "Carlito Bold" while its text reads "Carlito-Bold",
        the name inside the file. None when MuPDF can't load the font.
        """
        try:
            with self._mupdf_font_record(font_object) as font:
                return pymupdf.mupdf.ll_fz_font_name(font.font)
        except DriverError:  # MuPDF can't load the font
            return None

    @contextmanager
    def _mupdf_font_record(self, font_object: int) -> Iterator[pymupdf.mupdf.pdf_font_desc]:
        """MuPDF's own record of font `font_object`: its letter list, codes and glyph widths.

        MuPDF's C function pdf_load_font builds the record and pdf_drop_font
        frees it; Python never frees it, so it is only lent out inside a `with`.
        DriverError if MuPDF can't load the font.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        # MuPDF hangs loading a Type3 font with no resources to look in. Its drawings
        # aren't run here, so an empty set will do.
        no_resources = mu.pdf_new_dict(pdf, 0)
        resources = mu.pdf_resource_stack()
        resources.resources = no_resources.m_internal
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, resources, mu.pdf_load_object(pdf, font_object).m_internal
            )
        except MUPDF_ERRORS as exc:  # MuPDF can't make sense of the font object
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        try:
            yield font
        finally:
            mu.ll_pdf_drop_font(font)


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
        glyph=_glyph_of(font, cid),
        width=mu.ll_pdf_lookup_hmtx(font, cid).w,
    )


def _glyph_of(font: pymupdf.mupdf.pdf_font_desc, cid: int) -> GlyphId:
    """The shape a font MuPDF has loaded draws for `cid`; 0 for none."""
    mu = pymupdf.mupdf
    drawings = font.font.t3procs
    # Not a Type3 font: its glyph by the font's own map.
    if drawings is None:
        return GlyphId(mu.ll_pdf_font_cid_to_gid(font, cid))
    # A Type3 font's drawing per code, a C array read in place: its glyph is its code.
    drawn = ctypes.cast(int(drawings), ctypes.POINTER(ctypes.c_void_p))[cid]
    return GlyphId(cid if drawn else 0)


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
