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
from squidpdf.core.pdf.lowlevel.objects import PDF_NULL, PDF_REFERENCE, object_number, pdf_of
from squidpdf.core.types import FontCode, GlyphId

_ONE_BYTE_CODES = 256

_W_TOKEN = re.compile(r"\[|\]|-?\d+(?:\.\d+)?")

_RUN_TOKENS = 3

_TWO_BYTE_CODES = 65536

_WHOLE_NUMBER = re.compile(r"-?\d+")

_IDENTITY_ENCODINGS = ("/Identity-H", "/Identity-V")

_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")


@dataclass(frozen=True, slots=True, eq=False)
class FontRecords:
    doc: pymupdf.Document

    def font_codes(self, font_object: int, code_bytes: int) -> list[FontCode] | None:
        value_type, _value = self.doc.xref_get_key(font_object, "ToUnicode")
        if value_type == PDF_NULL:
            return None

        with self._mupdf_font_record(font_object) as font:
            if font.to_unicode is None:
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (_font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]

    def listed_widths(self, font_object: int) -> dict[str, float] | None:
        """A width of 0 is a code the file never draws, and is left out."""
        listed = self._listed_codes(font_object)
        if listed is None:
            return None
        widths: dict[str, float] = {}
        with self._mupdf_font_record(font_object) as font:
            for code in listed:
                cid = _cid_of(font, code)
                letter = _letter_of(font, code, cid)
                width = _width_of(font, cid)
                if letter is not None and width > 0:
                    widths.setdefault(letter, width)
        return widths

    def _listed_codes(self, font_object: int) -> range | list[int] | None:
        if self._entry(font_object, "Widths") is not None:
            first = _whole_number(self._entry(font_object, "FirstChar"))
            last = _whole_number(self._entry(font_object, "LastChar"))
            if first is None or last is None:
                return None
            return range(max(first, 0), min(last, _ONE_BYTE_CODES - 1) + 1)
        value_type, _value = self.doc.xref_get_key(font_object, "DescendantFonts")
        if value_type == PDF_NULL:
            return None
        # `/W` lists CIDs: only by Identity is a code its CID, so its letter can be read.
        if self._entry(font_object, "Encoding") not in _IDENTITY_ENCODINGS:
            return None
        inner = self.inner_font(font_object)
        if inner is None:
            return None
        w = self._entry(inner, "W")
        return None if w is None else _cids_in(w)

    def inner_font(self, font_object: int) -> int | None:
        value_type, value = self.doc.xref_get_key(font_object, "DescendantFonts")
        if value_type == PDF_REFERENCE:
            listed = object_number(value)
            # A damaged file can point past its last object.
            if not self._has_object(listed):
                return None
            value = self.doc.xref_object(listed, compressed=True)
        inner = _FIRST_REFERENCE.match(value)
        if inner is None:
            return None
        number = int(inner.group(1))
        return number if self._has_object(number) else None

    def _has_object(self, xref: int) -> bool:
        return 0 < xref < self.doc.xref_length()

    def _entry(self, xref: int, key: str) -> str | None:
        value_type, value = self.doc.xref_get_key(xref, key)
        if value_type == PDF_NULL:
            return None
        if value_type == PDF_REFERENCE:
            return self.doc.xref_object(object_number(value), compressed=True)
        return value

    def text_font_name(self, font_object: int) -> str | None:
        try:
            with self._mupdf_font_record(font_object) as font:
                return pymupdf.mupdf.ll_fz_font_name(font.font)
        except DriverError:
            return None

    @contextmanager
    def _mupdf_font_record(self, font_object: int) -> Iterator[pymupdf.mupdf.pdf_font_desc]:
        """Python never frees MuPDF's record (pdf_drop_font), so it's lent inside a `with`."""
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        # MuPDF hangs loading a Type3 font with no resources; unrun, empty ones do.
        no_resources = mu.pdf_new_dict(pdf, 0)
        resources = mu.pdf_resource_stack()
        resources.resources = no_resources.m_internal
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, resources, mu.pdf_load_object(pdf, font_object).m_internal
            )
        except MUPDF_ERRORS as exc:
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        try:
            yield font
        finally:
            mu.ll_pdf_drop_font(font)


def _font_code(font: pymupdf.mupdf.pdf_font_desc, value: int) -> FontCode | None:
    letter = _listed_letter_of(font, value)
    if letter is None:
        return None
    cid = _cid_of(font, value)
    return FontCode(
        value=value, letter=letter, glyph=_glyph_of(font, cid), width=_width_of(font, cid)
    )


def _listed_letter_of(font: pymupdf.mupdf.pdf_font_desc, code: int) -> str | None:
    codepoint = pymupdf.mupdf.ll_pdf_lookup_cmap(font.to_unicode, code)
    return chr(codepoint) if 0 <= codepoint <= sys.maxunicode else None


def _cid_of(font: pymupdf.mupdf.pdf_font_desc, code: int) -> int:
    return pymupdf.mupdf.ll_pdf_lookup_cmap(font.encoding, code)


def _width_of(font: pymupdf.mupdf.pdf_font_desc, cid: int) -> float:
    return pymupdf.mupdf.ll_pdf_lookup_hmtx(font, cid).w


def _glyph_of(font: pymupdf.mupdf.pdf_font_desc, cid: int) -> GlyphId:
    mu = pymupdf.mupdf
    drawings = font.font.t3procs
    if drawings is None:
        return GlyphId(mu.ll_pdf_font_cid_to_gid(font, cid))
    drawn = ctypes.cast(int(drawings), ctypes.POINTER(ctypes.c_void_p))[cid]
    return GlyphId(cid if drawn else 0)


def _letter_of(font: pymupdf.mupdf.pdf_font_desc, code: int, cid: int) -> str | None:
    if font.to_unicode is not None:
        return _listed_letter_of(font, code)
    if cid >= font.cid_to_ucs_len:
        return None
    codepoint = ctypes.cast(int(font.cid_to_ucs), ctypes.POINTER(ctypes.c_ushort))[cid]
    return chr(codepoint) if codepoint else None


def _cids_in(w: str) -> list[int] | None:
    tokens = _W_TOKEN.findall(w)[1:-1]
    cids: set[int] = set()
    place = 0
    try:
        while place < len(tokens):
            first = max(int(float(tokens[place])), 0)
            following = tokens[place + 1]
            if following == "[":
                closing = tokens.index("]", place + 2)
                last = first + closing - place - 3
                place = closing + 1
            else:
                last = int(float(following))
                place += _RUN_TOKENS
            cids.update(range(first, min(last, _TWO_BYTE_CODES - 1) + 1))
    except IndexError, ValueError:
        return None
    return sorted(cids)


def _whole_number(value: str | None) -> int | None:
    if value is None or not _WHOLE_NUMBER.fullmatch(value):
        return None
    return int(value)
