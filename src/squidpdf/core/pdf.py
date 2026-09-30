"""The only file that uses MuPDF's low-level API.

Everyday PyMuPDF calls (insert_text, get_pixmap, save) are easy to read, so
they stay in `core.mupdf`, whose driver extends this. The hard-to-read calls
live here, and their results come back as named dataclasses. This file only
reports what the PDF says; the engine decides what to do with it.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymupdf

from squidpdf.core.types import FontCode, FontDescriptor, GlyphId, PageFont, Rect, TextPiece

__all__ = [
    "MUPDF_ERRORS",
    "PdfFile",
]

_BYTE_MAX = 255  # the top of one color channel in 0xRRGGBB

# The object the first entry of an array points at: "[15 0 R]" -> 15.
_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")
# Where a font description keeps the font file, by the kind of file it is.
_FONT_FILES = ("FontFile2", "FontFile3")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

_ONE_BYTE_CODES = 256  # a simple font has codes 0-255
_STRIP_PAD_PT = 0.1  # how far an erased strip reaches past the points it runs through
# Where an erased strip runs: this share of each letter's height up from its baseline.
# Every font's glyph boxes reach just above the baseline; few reach the next line's.
_STRIP_LIFTS = (0.05, 0.2)
_OPACITY_PREFIX = "SquidOpacity"  # our graphics states' names, e.g. SquidOpacity600 for 0.6
_PERMILLE = 1000  # opacity is written to a thousandth, far finer than the eye sees

# The catalog entry for the file's tags: the reading order a screen reader follows.
_TAGS_KEY = "StructTreeRoot"
_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# What PyMuPDF raises when MuPDF can't do what it was asked: MuPDF's own errors,
# which aren't RuntimeErrors, and the RuntimeErrors and ValueErrors PyMuPDF adds.
MUPDF_ERRORS = (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError)


@dataclass(frozen=True, slots=True)
class Letter:
    """One letter as get_text("rawdict") reads it, with its line's direction."""

    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1, from ascender to descender
    origin: tuple[float, float]  # where it starts, on its baseline
    direction: tuple[float, float]  # the way its line reads: (1, 0) is left to right


class PdfFile:
    """An open PDF, with simple methods to read and change it."""

    def __init__(self, doc: pymupdf.Document) -> None:
        """Wrap an open document. The caller still closes it."""
        self._doc = doc

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in."""
        blocks = self._doc[page].get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        return [
            [text_piece(raw, direction=line["dir"]) for raw in line["spans"]]
            for line in each_line(blocks)
        ]

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]:
        """The letters inside each box on the page, in reading order.

        A letter counts when its middle is inside, so one that grazes the edge doesn't.
        """
        letters = self._letters(page)
        return [letters_inside(letters, box) for box in boxes]

    def _letters(self, page: int) -> list[Letter]:
        """Every letter on the page, in reading order."""
        blocks = self._doc[page].get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]
        return list(each_letter(blocks))

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
        except MUPDF_ERRORS:  # MuPDF can't read the font's stream out
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
        # A TrueType face is stored as FontFile2, an OpenType one (Latin Modern) as FontFile3.
        stored = (self._doc.xref_get_key(owner, f"FontDescriptor/{key}") for key in _FONT_FILES)
        value = next(value for kind, value in stored if kind == "xref")
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

        with self._mupdf_font_record(xref) as font:
            if font.to_unicode is None:  # MuPDF couldn't read it
                return None
            if code_bytes == 1:
                code_count = _ONE_BYTE_CODES
            else:  # Type0: one code per glyph, so stop after the last glyph
                code_count = font.cid_to_gid_len or font.font.glyph_count
            codes = (font_code(font, value) for value in range(code_count))
            return [code for code in codes if code is not None]

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` is read under, often the font file's own.

        A page can list a font as "Carlito Bold" while its text reads "Carlito-Bold",
        the name inside the file. None when MuPDF can't load the font.
        """
        try:
            with self._mupdf_font_record(xref) as font:
                return pymupdf.mupdf.ll_fz_font_name(font.font)
        except ValueError:  # MuPDF can't load the font
            return None

    @contextmanager
    def _mupdf_font_record(self, xref: int) -> Iterator[pymupdf.mupdf.pdf_font_desc]:
        """MuPDF's own record of font `xref`: its letter list, codes and glyph widths.

        MuPDF's C function pdf_load_font builds the record and pdf_drop_font
        frees it; Python never frees it, so it is only lent out inside a `with`.
        ValueError if MuPDF can't load the font.
        """
        mu = pymupdf.mupdf
        pdf = self._pdf()
        try:
            font = mu.ll_pdf_load_font(
                pdf.m_internal, None, mu.pdf_load_object(pdf, xref).m_internal
            )
        except MUPDF_ERRORS as exc:  # MuPDF can't make sense of the font object
            raise ValueError(f"MuPDF can't load font {xref}") from exc
        try:
            yield font
        finally:
            mu.ll_pdf_drop_font(font)

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Delete the letters whose middle is inside each box. Images, drawings and links stay.

        The same letters `text_in` reads, so what's erased is what's checked.
        MuPDF deletes every letter whose box a redaction touches, and a letter's
        box runs from its font's ascender to its descender: at usual line spacing
        it reaches the lines above and below. So each box is erased as a thin
        strip just above its own letters' baselines, which other lines' boxes
        don't reach. A box that still has letters afterwards (a font whose boxes
        sit oddly) is erased whole, so old text is never left under new. MuPDF
        also deletes any link a redaction touches: those are put back.
        """
        links = self._doc[page].get_links()
        letters = self._letters(page)
        # No letter's middle inside: erase the whole box, as nothing else would.
        self._redact(page, [strip_through(letters, box) or box for box in boxes])
        left = self.text_in(page, boxes)
        missed = [box for box, text in zip(boxes, left, strict=True) if text.strip()]
        if missed:
            self._redact(page, missed)
        self._restore_links(page, links)

    def _restore_links(self, page: int, links: list[dict]) -> None:
        """Add back any of `links`, as get_links read them, that the page no longer has."""
        pg = self._doc[page]
        kept = {link_key(link) for link in pg.get_links()}
        for link in links:
            if link_key(link) not in kept:
                pg.insert_link(link)

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`."""
        pg = self._doc[page]
        areas = [pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) for box in boxes]
        for link in pg.get_links():
            if any(pymupdf.Rect(link["from"]).intersects(area) for area in areas):
                pg.delete_link(link)

    def _redact(self, page: int, boxes: list[Rect]) -> None:
        """Delete every letter whose box touches one of `boxes`, and nothing else."""
        pg = self._doc[page]
        for box in boxes:
            pg.add_redact_annot(pymupdf.Rect(box.x0, box.y0, box.x1, box.y1))
        pg.apply_redactions(
            images=pymupdf.mupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.mupdf.PDF_REDACT_LINE_ART_NONE,
        )

    def has_tags(self) -> bool:
        """Whether the file is tagged: it has the reading order a screen reader follows."""
        catalog = self._doc.pdf_catalog()  # the file's root entry, where the tags are listed
        kind, _value = self._doc.xref_get_key(xref=catalog, key=_TAGS_KEY)
        return kind != _PDF_NULL

    def drop_tags(self) -> None:
        """Remove the file's tags. Saving then drops every page only they pointed at."""
        catalog = self._doc.pdf_catalog()  # the file's root entry, where the tags are listed
        self._doc.xref_set_key(xref=catalog, key=_TAGS_KEY, value=_PDF_NULL)

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


def font_code(font: pymupdf.mupdf.pdf_font_desc, value: int) -> FontCode | None:
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


def each_line(blocks: list[dict]) -> Iterator[dict]:
    """Every line of text get_text read, in reading order."""
    for block in blocks:
        yield from block["lines"]


def each_letter(blocks: list[dict]) -> Iterator[Letter]:
    """Every letter get_text("rawdict") read, in reading order."""
    for line in each_line(blocks):
        direction = (line["dir"][0], line["dir"][1])
        for piece in line["spans"]:
            for char in piece["chars"]:
                origin = (char["origin"][0], char["origin"][1])
                yield Letter(char["c"], tuple(char["bbox"]), origin, direction)


def text_piece(raw: dict, *, direction: tuple[float, float]) -> TextPiece:
    """One piece of text from get_text("dict"), named, on a line that reads `direction`."""
    return TextPiece(
        text=raw["text"],
        font=raw["font"],
        size=raw["size"],
        color=rgb(raw["color"]),
        opacity=raw["alpha"] / _BYTE_MAX,
        box=Rect(*raw["bbox"]),
        origin=(raw["origin"][0], raw["origin"][1]),
        direction=(direction[0], direction[1]),
    )


def letters_inside(letters: list[Letter], box: Rect) -> str:
    """The letters whose middle is inside `box`, in order."""
    return "".join(letter.text for letter in letters if middle_inside(letter.box, box))


def link_key(link: dict) -> tuple[tuple[str, str], ...]:
    """What a link from get_links is: where it sits and where it goes, not its object number."""
    return tuple(
        sorted((key, repr(value)) for key, value in link.items() if key not in ("xref", "id"))
    )


def middle_of(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """The middle of a box given as x0, y0, x1, y1."""
    x0, y0, x1, y1 = bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def middle_inside(bbox: tuple[float, float, float, float], box: Rect) -> bool:
    """Whether the middle of `bbox` lies inside `box`."""
    x, y = middle_of(bbox)
    return box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1


def strip_through(letters: list[Letter], box: Rect) -> Rect | None:
    """A thin box along the letters whose middle is in `box`, just above their baselines.

    None when no letter's middle is there.
    """
    points = [
        point
        for letter in letters
        if middle_inside(letter.box, box)
        for point in lifted(letter)
    ]
    if not points:
        return None
    xs, ys = [x for x, _y in points], [y for _x, y in points]
    pad = _STRIP_PAD_PT
    return Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def lifted(letter: Letter) -> list[tuple[float, float]]:
    """Points over the letter's middle, each _STRIP_LIFTS of its height up from its baseline.

    Up is across the line's direction, so a line turned on the page works too.
    """
    x0, y0, x1, y1 = letter.box
    along_x, along_y = letter.direction
    up_x, up_y = along_y, -along_x  # the page's y grows downward
    width = abs(x1 - x0) * abs(along_x) + abs(y1 - y0) * abs(along_y)
    height = abs(x1 - x0) * abs(up_x) + abs(y1 - y0) * abs(up_y)
    x, y = letter.origin
    middle_x, middle_y = x + along_x * width / 2, y + along_y * width / 2
    return [
        (middle_x + up_x * height * lift, middle_y + up_y * height * lift)
        for lift in _STRIP_LIFTS
    ]


def rgb(packed: int) -> tuple[float, float, float]:
    """A 0xRRGGBB color as r, g, b, each 0-1."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
