"""The only file that uses MuPDF's low-level API.

Everyday PyMuPDF calls (insert_text, get_pixmap, save) are easy to read, so
they stay in `core.mupdf`, whose backend extends this. The hard-to-read calls
live here, and their results come back as named dataclasses. This file only
reports what the PDF says; the engine decides what to do with it.
"""

from __future__ import annotations

import re
import sys

import pymupdf

from squidpdf.core.types import FontCode, FontDescriptor, GlyphId, PageFont, Rect, TextPiece

_BYTE_MAX = 255  # the top of one color channel in 0xRRGGBB

# The object the first entry of an array points at: "[15 0 R]" -> 15.
_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255

# What PyMuPDF raises when MuPDF can't do what it was asked: MuPDF's own errors,
# which aren't RuntimeErrors, and the RuntimeErrors and ValueErrors PyMuPDF adds.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)


class PdfFile:
    """An open PDF, with simple methods to read and change it."""

    def __init__(self, doc: pymupdf.Document) -> None:
        """Wrap an open document. The caller still closes it."""
        self._doc = doc

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in."""
        lines = []
        for block in self._doc[page].get_text("dict", flags=_TEXT_FLAGS)["blocks"]:
            for line in block["lines"]:
                lines.append([_text_piece(raw) for raw in line["spans"]])
        return lines

    def text_in(self, page: int, box: Rect) -> str:
        """The letters drawn inside `box` on the page, in reading order.

        A letter counts when its middle is inside, so one on the next line that
        only grazes the box's edge doesn't.
        """
        blocks = self._doc[page].get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]
        letters = (
            char
            for block in blocks
            for line in block["lines"]
            for piece in line["spans"]
            for char in piece["chars"]
        )
        return "".join(char["c"] for char in letters if _middle_inside(char["bbox"], box))

    def fonts(self, page: int) -> list[PageFont]:
        """Every font the page uses, including inside forms."""
        listed = self._doc[page].get_fonts(full=True)
        return [
            PageFont(
                xref=xref,
                name=name,
                kind=kind,
                file_type=file_type,
                resource=resource,
                encoding=encoding,
                in_form=referencer != 0,
            )
            for xref, file_type, kind, name, resource, encoding, referencer in listed
        ]

    def font_bytes(self, xref: int) -> bytes | None:
        """The font file stored in the PDF, or None if MuPDF can't read it."""
        try:
            _name, _ext, _kind, buffer = self._doc.extract_font(xref)
        except MUPDF_ERRORS:
            return None
        return buffer or None

    def font_descriptor(self, xref: int) -> FontDescriptor | None:
        """What the font's description says about how it looks; None when it has none.

        A font only named by one of the 14 standard names often has none. A
        two-byte (Type0) font keeps it on the font inside it.
        """
        owner = self._describing_font(xref)
        if owner is None:
            return None
        kind, _value = self._doc.xref_get_key(owner, "FontDescriptor")
        if kind == "null":
            return None
        flags = self._number(owner, "FontDescriptor/Flags")
        angle = self._number(owner, "FontDescriptor/ItalicAngle")
        return FontDescriptor(
            flags=0 if flags is None else int(flags),
            weight=self._number(owner, "FontDescriptor/FontWeight"),
            italic_angle=0.0 if angle is None else angle,
        )

    def replace_font_file(self, xref: int, font_file: bytes) -> None:
        """Swap in a new file for font `xref`. It must keep each glyph at its old number."""
        owner = self._describing_font(xref)
        if owner is None:  # MuPDF always points at the inner font, so this is someone else's
            raise ValueError(f"font {xref} has its inner font written out in place")
        _kind, value = self._doc.xref_get_key(owner, "FontDescriptor/FontFile2")
        file_xref = int(value.split()[0])  # "7 0 R" -> 7
        self._doc.update_stream(file_xref, font_file)
        # A TrueType file states its size before compression, too.
        self._doc.xref_set_key(file_xref, "Length1", str(len(font_file)))

    def _describing_font(self, xref: int) -> int | None:
        """Where the font's description lives: the font itself, or a Type0's inner font."""
        kind, value = self._doc.xref_get_key(xref, "DescendantFonts")
        # A simple font describes itself.
        if kind != "array":
            return xref
        inner = _FIRST_REFERENCE.match(value)
        # None when written out in place: rare, and not worth it.
        return int(inner.group(1)) if inner else None

    def _number(self, xref: int, key: str) -> float | None:
        """A number in object `xref` at `key`, or None when it isn't there or isn't a number."""
        kind, value = self._doc.xref_get_key(xref, key)
        if kind not in ("int", "real"):
            return None
        return float(value)

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first.

        None if the font has no letter list (its ToUnicode) or MuPDF can't read
        that list; ValueError if MuPDF can't load the font at all. `code_bytes`
        is 1 for a simple font, 2 for a Type0 (two-byte) font.
        """
        value_type, _value = self._doc.xref_get_key(xref, "ToUnicode")
        if value_type == "null":
            return None

        mu = pymupdf.mupdf
        pdf = self._pdf()
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, None, mu.pdf_load_object(pdf, xref).m_internal
            )
        except MUPDF_ERRORS as exc:
            raise ValueError(f"MuPDF can't load font {xref}") from exc
        try:
            if font.to_unicode is None:  # MuPDF couldn't read it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Type0: one code per glyph, so stop after the last glyph
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (_font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]
        finally:
            mu.ll_pdf_drop_font(font)

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Delete the text inside these boxes. Images and drawings stay."""
        pg = self._doc[page]
        for box in boxes:
            pg.add_redact_annot(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1))
        pg.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
        )

    def restore_font(self, page: int, resource: str, xref: int) -> None:
        """Point the page's font name `resource` back at font `xref`.

        Erasing text can remove a font the page no longer uses. This puts
        the same font back under the same name; nothing new is added.
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

    def to_pdf_space(self, page: int, point: tuple[float, float]) -> tuple[float, float]:
        """Turn a point on the page as you see it into the PDF's own coordinates.

        Not page.transformation_matrix: on a turned page it forgets where the
        page's box starts.
        """
        mu = pymupdf.mupdf
        pg = self._doc[page]
        _mediabox, page_to_screen = mu.FzRect(), mu.FzMatrix()
        mu.pdf_page_transform(mu.pdf_page_from_fz_page(pg.this), _mediabox, page_to_screen)
        moved = pymupdf.Point(*point) * pg.rotation_matrix * ~pymupdf.Matrix(page_to_screen)
        return (moved.x, moved.y)

    def add_content(self, page: int, stream: bytes) -> None:
        """Draw `stream` on top of everything on the page.

        The page's own drawing is wrapped first, so its settings (color,
        position) can't leak into ours.
        """
        pg = self._doc[page]
        if not pg.is_wrapped:
            pg.wrap_contents()
        xref = self._doc.get_new_xref()
        self._doc.update_object(xref, "<<>>")
        self._doc.update_stream(xref, stream)
        parts = [*pg.get_contents(), xref]
        self._doc.xref_set_key(
            pg.xref, "Contents", "[" + " ".join(f"{p} 0 R" for p in parts) + "]"
        )

    def _pdf(self) -> pymupdf.mupdf.PdfDocument:
        """The same document, as MuPDF's low-level API needs it."""
        return pymupdf.mupdf.pdf_document_from_fz_document(self._doc.this)


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


def _text_piece(raw: dict) -> TextPiece:
    """One piece of text from get_text("dict"), with named fields."""
    return TextPiece(
        text=raw["text"],
        font=raw["font"],
        size=raw["size"],
        color=_rgb(raw["color"]),
        box=Rect(*raw["bbox"]),
        origin=(raw["origin"][0], raw["origin"][1]),
    )


def _middle_inside(bbox: tuple[float, float, float, float], box: Rect) -> bool:
    """Whether the middle of `bbox` lies inside `box`."""
    x0, y0, x1, y1 = bbox
    return box.x0 <= (x0 + x1) / 2 <= box.x1 and box.y0 <= (y0 + y1) / 2 <= box.y1


def _rgb(packed: int) -> tuple[float, float, float]:
    """A 0xRRGGBB color as r, g, b, each 0-1."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
