"""The one driver there is: PyMuPDF.

Only primitives here (see `core.pdf.driver`): the hard-to-read MuPDF calls come
from `core.pdf.lowlevel`, whose `PdfFile` the driver holds, and the everyday ones are below.
Only `core.engine` opens the driver: nothing outside `core` learns that MuPDF is underneath.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import cache, reduce
from itertools import chain, count
from operator import or_
from typing import Any

import pymupdf

from squidpdf.core.app.errors import Damaged, Encrypted, Failure, TooHeavy, machine_failure
from squidpdf.core.app.message import Message
from squidpdf.core.constants import GARBAGE_COLLECT
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.pdf.lowlevel import (
    MUPDF_ERRORS,
    MUPDF_OWN_ERRORS,
    MUPDF_SYSTEM_ERRORS,
    MUPDF_TOO_HEAVY,
    PdfFile,
)
from squidpdf.core.types import (
    QUARTER_TURNS,
    SOLID,
    CodeRun,
    Face,
    FontCode,
    FontDescriptor,
    FontFileType,
    FontKind,
    FontResource,
    FormField,
    HiddenCopy,
    Page,
    PageFont,
    QuarterTurn,
    Rect,
    TextPiece,
    TextRun,
)

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel
_BYTE_MAX = 255  # the top of one color channel in 0xRRGGBB
# The bytes a PDF name holds as they are: printable, and none that ends a name or
# starts an escape. Any other is written #xx.
_PRINTABLE = range(0x21, 0x7F)
_ENDS_OR_ESCAPES_A_NAME = b"()<>[]{}/%#"

# The object the first entry of an array points at: "[15 0 R]" -> 15.
_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")
# Where a font description keeps the font file, by the kind of file it is.
_FONT_FILES = ("FontFile2", "FontFile3")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
_NAME_BYTES_KEPT = 31  # how much of a font's name MuPDF keeps for the text it reads

# A font's kind, as the file names it, in our words. A multiple-master font is a Type 1.
_KINDS: dict[str, FontKind] = {
    "TrueType": "truetype",
    "Type0": "type0",
    "Type1": "type1",
    "MMType1": "type1",
    "Type3": "type3",
}
# The name MuPDF's text gives a Type3 font with no /Name, by its object.
_UNNAMED_TYPE3 = "Type3 ({xref} 0 R)"
# How a font's program is stored, as PyMuPDF names it, in our words. "cid" is a CFF
# whose shapes are numbered, not named.
_FILE_TYPES: dict[str, FontFileType] = {
    "ttf": "truetype",
    "otf": "opentype",
    "cff": "cff",
    "cid": "cff",
    "pfa": "type1",
}

# What MuPDF's list of a page's drawing calls names a picture or shading; paths come apart.
_PICTURE_KINDS = ("fill-image", "fill-imgmask", "fill-shade")

# The kinds of form field that show their value as text, as PyMuPDF names them.
_TEXT_FIELD_KINDS = ("Text", "ComboBox", "ListBox")

_STRIP_PAD_PT = 0.1  # how far an erased strip reaches past the points it runs through
# Where an erased strip runs: this share of each letter's height up from its baseline.
# Every font's glyph boxes reach just above the baseline; few reach the next line's.
_STRIP_LIFTS = (0.05, 0.2)

# The catalog entry for the file's tags: the reading order a screen reader follows.
_TAGS_KEY = "StructTreeRoot"
_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# MuPDF's failures, matched in order with `isinstance`, and the first match wins: its own
# errors all derive from one, so the narrower ones go first.
MUPDF_FAILURES = (
    Failure(raised=MUPDF_TOO_HEAVY, problem=TooHeavy),  # past a limit of MuPDF's own
    # Out of memory, or a file it can't open: only its words tell which.
    Failure(raised=MUPDF_SYSTEM_ERRORS, problem=machine_failure),
    Failure(raised=MUPDF_OWN_ERRORS, problem=Damaged),  # anything else it couldn't make out
)

# The library and its version, for `core.engine`'s BUILD.
DRIVER_BUILD = f"mupdf-{pymupdf.mupdf_version}"


@dataclass(frozen=True, slots=True, eq=False)
class _MuPDFFont:
    """A font file MuPDF has opened, made by `_mupdf_font`. Implements `FontProgram`."""

    font: pymupdf.Font
    # Each letter's width in ems, by its code point, read once: a fit check measures
    # the same few letters thousands of times.
    width_in_ems: Callable[[int], float] = field(repr=False)

    def listed_letters(self) -> list[int]:
        """Every code point MuPDF says the font maps; a trimmed font lists more."""
        return list(self.font.valid_codepoints())

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        return self.width_in_ems(ord(ch))

    def maps(self, ch: str) -> bool:
        """Whether the font has a glyph of its own for `ch`, even an empty one."""
        return self.font.has_glyph(ord(ch)) != 0  # 0 is .notdef: MuPDF found none

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points: what MuPDF's `text_length` says, quicker."""
        return sum(map(self.width_in_ems, map(ord, text))) * size


def _mupdf_font(font_file: bytes) -> _MuPDFFont:
    """`font_file` opened in MuPDF, to measure with. Raises MuPDF's error if it isn't a font."""
    font = pymupdf.Font(fontbuffer=font_file)
    return _MuPDFFont(font, cache(font.glyph_advance))


@cache
def open_face(face: Face) -> FontProgram:
    """A face we ship, opened once per process: it measures what `add_font` draws."""
    return _mupdf_font(face_bytes(face))


def open_driver(path: str) -> PdfDriver:
    """The PDF at `path`, open in MuPDF, only ever as a PDF.

    Raises FileNotFoundError when it's gone, Encrypted behind a password, and
    Damaged when it isn't a PDF anyone could show.
    """
    try:
        # Left to sniff, MuPDF opens a PNG as a document.
        doc = pymupdf.open(path, filetype="pdf")
    except pymupdf.FileNotFoundError as exc:  # gone: Python's own error, which callers catch
        raise FileNotFoundError(path) from exc
    except pymupdf.FileDataError as exc:  # garbage, truncated or empty
        raise Damaged from exc
    if doc.needs_pass:  # it opens, but every page is locked behind a password
        doc.close()
        raise Encrypted
    try:
        page_count = len(doc)
    except MUPDF_ERRORS as exc:  # its page list can't even be counted
        doc.close()
        raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None
    if page_count == 0:  # no page to show or edit: not a document anyone made
        doc.close()
        raise Damaged(debug="no pages")
    return _MuPDFDriver(doc, PdfFile(doc))


@dataclass(frozen=True, slots=True, eq=False)
class _MuPDFDriver:
    """A PDF open in MuPDF. Implements `core.pdf.driver.PdfDriver`. Made by `open_driver`."""

    doc: pymupdf.Document
    file: PdfFile  # the same document, for the calls MuPDF's low-level API makes
    # What this driver did to each page, by its object, whose number survives renumbering.
    changed: dict[int, _ChangedPage] = field(default_factory=dict, repr=False)

    def page_count(self) -> int:
        """How many pages the document has, without reading any of them."""
        return len(self.doc)

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for.

        Raises Damaged when the file counts pages it doesn't have.
        """
        try:
            pages = [self.doc[pno] for pno in range(len(self.doc))]
        except (*MUPDF_ERRORS, IndexError) as exc:  # its page list says pages it doesn't hold
            raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None
        # MuPDF reads /Rotate, a clockwise turn, as a quarter turn: 0, 90, 180 or 270.
        return [Page(p.cropbox.width, p.cropbox.height, p.rotation) for p in pages]

    def page_image(self, page: int, scale: float) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point."""
        pdf_page = self.doc[page]
        pix = pdf_page.get_pixmap(
            matrix=pdf_page.derotation_matrix * pymupdf.Matrix(scale, scale), alpha=False
        )
        return pix.tobytes("png")

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]:
        """Each box of the page unrotated as a PNG, `scale` pixels per point.

        Draws the area around them all once and cuts each box out, since any
        drawing runs the whole page. The clip is in the rotated page, so each box is turned.
        """
        pdf_page = self.doc[page]
        matrix = pdf_page.derotation_matrix * pymupdf.Matrix(scale, scale)
        turned = [
            pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) * pdf_page.rotation_matrix
            for box in boxes
        ]
        drawn = pdf_page.get_pixmap(matrix=matrix, clip=reduce(or_, turned), alpha=False)
        return [_cut(drawn, (box * matrix).irect) for box in turned]

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in."""
        pdf_page = self.doc[page]
        blocks = pdf_page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        full_names = _full_names([font[3] for font in pdf_page.get_fonts(full=True)])
        return [
            [
                _text_piece(raw, direction=line["dir"], full_names=full_names)
                for raw in line["spans"]
            ]
            for line in _each_line(blocks)
        ]

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]:
        """The letters inside each box on the page, in reading order.

        A letter counts when its middle is inside, so one that grazes the edge doesn't.
        """
        letters = self._letters(page)
        return ["".join(letter.text for letter in letters.inside(box)) for box in boxes]

    def drawn_boxes(self, page: int) -> list[Rect]:
        """The box around each thing the page draws but text: lines, shapes and pictures.

        A path is boxed piece by piece, so a grid drawn as one path still has its lines.
        """
        pdf_page = self.doc[page]
        pieces = [
            _box_of(piece, ink=_ink_of(drawing))
            for drawing in pdf_page.get_drawings()
            for piece in drawing["items"]
        ]
        pictures = [
            Rect(*box) for kind, box in pdf_page.get_bboxlog() if kind in _PICTURE_KINDS
        ]
        return pieces + pictures

    def form_fields(self, page: int) -> list[FormField]:
        """Each form field on the page that shows text, and the value it shows.

        A field draws its value itself, not the page. A check box, a button, a
        signature or an empty field shows no value, so it isn't listed.
        """
        # No form in the file at all, as in most: no page need be asked.
        if not self.doc.is_form_pdf:
            return []
        return [
            FormField(Rect(*field.rect), field.field_value)
            for field in self.doc[page].widgets()
            if _shows_text(field.field_type_string, field.field_value)
        ]

    def _letters(self, page: int) -> _PageLetters:
        """Every letter on the page, to find by where it sits."""
        blocks = self.doc[page].get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]
        return _page_letters(list(_each_letter(blocks)))

    def fonts(self, page: int) -> list[PageFont]:
        """Every font the page uses, forms included; a Type3 by the name its text reads."""
        listed = self.doc[page].get_fonts(full=True)
        return [
            PageFont(
                xref=xref,
                name=name if kind != "Type3" else self._type3_name(xref),
                # .get: a font with no kind, or one the PDF format has no name for.
                kind=_KINDS.get(kind, "other"),
                # .get: "n/a" when only named, or stored in a way MuPDF can't name.
                file_type=_FILE_TYPES.get(file_type, "none"),
                resource=resource,
                encoding=encoding,
                in_form=referencer != 0,
            )
            for xref, file_type, kind, name, resource, encoding, referencer in listed
        ]

    def _type3_name(self, xref: int) -> str:
        """A Type3 font's name as its text reads it: its /Name, never the /BaseFont listed."""
        value_type, value = self.doc.xref_get_key(xref, "Name")
        # No /Name, which a Type3 font needn't have.
        if value_type != "name":
            return _UNNAMED_TYPE3.format(xref=xref)
        return value.removeprefix("/")

    def font_bytes(self, xref: int) -> bytes:
        """The font file stored in the PDF. Raises DriverError when MuPDF can't read it out."""
        try:
            _name, _ext, _kind, buffer = self.doc.extract_font(xref)
        except MUPDF_ERRORS as exc:  # MuPDF can't read the font's stream out
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        # Stored, but empty: nothing to draw with.
        if not buffer:
            raise DriverError(Message("font_unreadable"), debug=f"font {xref} is empty")
        return buffer

    def font_descriptor(self, xref: int) -> FontDescriptor | None:
        """What the font's description says about how it looks; None when it has none.

        A font only named by one of the 14 standard names often has none. A
        two-byte (Type0) font keeps it on the font inside it.
        """
        owner = self._describing_font(xref)
        if owner is None:
            return None
        value_type, _value = self.doc.xref_get_key(owner, "FontDescriptor")
        if value_type == "null":
            return None
        flags = self._number(owner, "FontDescriptor/Flags")
        angle = self._number(owner, "FontDescriptor/ItalicAngle")
        return FontDescriptor(
            flags=0 if flags is None else int(flags),
            weight=self._number(owner, "FontDescriptor/FontWeight"),
            italic_angle=0.0 if angle is None else angle,
        )

    def _describing_font(self, xref: int) -> int | None:
        """Where the font's description lives: the font itself, or a Type0's inner font.

        None when a Type0's inner font isn't an object of its own in the file.
        """
        value_type, value = self.doc.xref_get_key(xref, "DescendantFonts")
        # A simple font describes itself.
        if value_type not in ("array", "xref"):
            return xref
        # The list of inner fonts kept as an object of its own ("12 0 R"): read it there.
        if value_type == "xref":
            list_xref = _object_number(value)
            # A damaged file can point past its last object: there's no list to read.
            if not self._has_object(list_xref):
                return None
            value = self.doc.xref_object(list_xref, compressed=True)
        inner = _FIRST_REFERENCE.match(value)
        # Written out in place: rare, and not worth it.
        if inner is None:
            return None
        inner_xref = int(inner.group(1))
        # Pointing past the file's last object, as a damaged file can: nothing to describe it.
        return inner_xref if self._has_object(inner_xref) else None

    def _has_object(self, xref: int) -> bool:
        """Whether the file has an object numbered `xref`: none is 0, or past its last."""
        return 0 < xref < self.doc.xref_length()

    def _number(self, xref: int, key: str) -> float | None:
        """A number in object `xref` at `key`, or None when it isn't there or isn't a number."""
        value_type, value = self.doc.xref_get_key(xref, key)
        # PyMuPDF calls a number with a fraction (a PDF's real) a "float".
        if value_type not in ("int", "float"):
            return None
        return float(value)

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first; None without a letter list.

        Read from MuPDF's own record of the font. Raises DriverError when MuPDF
        can't load it.
        """
        return self.file.font_codes(xref, code_bytes)

    def listed_widths(self, xref: int) -> dict[str, float] | None:
        """Each letter the font's width list gives a width, per 1000 em; None without a list.

        Read from MuPDF's own record of the font. Raises DriverError when MuPDF
        can't load it.
        """
        return self.file.listed_widths(xref)

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` reads, often the font file's own; None if unreadable.

        Read from MuPDF's own record of the font.
        """
        return self.file.text_font_name(xref)

    def open_font(self, font_file: bytes) -> _MuPDFFont:
        """Open a font file to measure with. Raises DriverError when it isn't one."""
        # No bytes at all: MuPDF would open its own Noto Serif in their place.
        if not font_file:
            raise DriverError(Message("font_unreadable"), debug="no bytes")
        try:
            return _mupdf_font(font_file)
        except MUPDF_ERRORS as exc:  # MuPDF can't read the bytes as a font
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc

    def face_font(self, face: Face) -> FontProgram:
        """A face we ship, opened to measure with: it measures what `add_font` draws."""
        return open_face(face)

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """Add a font to the page under the resource name `resource`, or one like it if taken.

        Then `resource` numbered past any the page has: MuPDF, given a name the
        page uses, hands back the font already there. Raises DriverError when
        MuPDF won't add it.
        """
        free_name = self._free_name(page, resource)
        pdf_page = self.doc[page]
        try:
            xref = pdf_page.insert_font(fontname=free_name, fontbuffer=font_file)
        except MUPDF_ERRORS as exc:  # the bytes opened as a font, but the page won't take them
            raise DriverError(Message("font_not_added"), debug=str(exc)) from exc
        self._changed_page(page).keep_named(free_name, xref)
        return FontResource(free_name, xref)

    def _changed_page(self, page: int) -> _ChangedPage:
        """What this driver did to the page so far, kept from the first change on."""
        return self.changed.setdefault(self.doc[page].xref, _ChangedPage())

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        """Delete the letters whose middle is inside each box, for real.

        Each box is erased as a thin strip above its letters' baselines, since a redaction
        takes every letter its box touches, and a letter's box can reach the lines around it.
        A box with letters left is then erased whole, so old text is never left under new.
        """
        letters = self._letters(page)
        with self.file.links_kept(page):
            # No letter's middle inside: erase the whole box, as nothing else would.
            self.file.redact(page, [_strip_along(letters.inside(box)) or box for box in boxes])
            left = self.text_in(page, boxes)
            missed = [box for box, text in zip(boxes, left, strict=True) if text.strip()]
            # Read again only after a second erase: most boxes are clear after the first.
            if missed:
                self.file.redact(page, missed)
                left = self.text_in(page, boxes)
        for resource, xref in self._changed_page(page).named.items():
            self.file.restore_font(page, resource, xref)
        return left

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`, whatever it does."""
        self.file.drop_links(page, boxes)

    def hidden_copies(self, page: int) -> list[str]:
        """Every hidden copy on the page, as text, and each string MuPDF may miss."""
        return self.file.hidden_copies(page)

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy on the page; one blanked goes."""
        self.file.rewrite_hidden_copies(page, rewritten)

    def document_hidden_copies(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        """Each of the document's own hidden copies that `holding` is true of, and where."""
        return self.file.document_hidden_copies(holding)

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(copy)` for the document's hidden copies; one left blank goes."""
        self.file.rewrite_document_hidden_copies(rewritten)

    def is_signed(self) -> bool:
        """Whether the document holds a signature, or anything `drop_signatures` deletes."""
        return self.file.is_signed()

    def drop_signatures(self) -> None:
        """Delete every signature, and what the file keeps only to check one."""
        self.file.drop_signatures()

    def write_codes(
        self,
        page: int,
        *,
        origin: tuple[float, float],
        runs: Sequence[CodeRun],
        size: float,
        color: tuple[float, float, float],
        opacity: float,
        scale_x: float,
        turn_ccw: QuarterTurn,
    ) -> None:
        """Write each run's codes in its font, from `origin` on, on top of the page.

        One text object: each run switches to its font, and the pen moves on by
        that font's widths. PyMuPDF's writers only take letters, so it's written
        out here.
        """
        x, y = self.file.to_pdf_space(page, origin)
        resources = self._resources_of(page, {run.xref for run in runs})
        shown = " ".join(
            f"{_written_name(resources[run.xref])} {size:.{_PDF_DP}f} Tf <{run.codes.hex()}> Tj"
            for run in runs
        )
        # See-through: a graphics state that paints at `opacity`.
        paint = f" /{self.file.add_opacity(page, opacity)} gs" if opacity < SOLID else ""
        # Where the text goes: narrowed along its line, turned, and placed.
        cos, sin = QUARTER_TURNS[turn_ccw]
        matrix = [scale_x * cos, scale_x * sin, -sin, cos, x, y]
        placed = " ".join(f"{number:.{_PDF_DP}f}" for number in matrix)
        color_operands = " ".join(f"{channel:.{_PDF_DP}f}" for channel in color)
        # Save the page's settings, set color, place the text, write each run in
        # its font and size, then put the settings back.
        stream = f"q{paint} BT {color_operands} rg {placed} Tm {shown} ET Q"
        self._add_content(page, stream.encode())

    def _resources_of(self, page: int, xrefs: set[int]) -> dict[int, str]:
        """The page's resource name for each font in `xrefs`, giving one to any it lacks.

        It lacks one when erasing dropped the font, or when it's another page's copy.
        """
        named: dict[int, str] = {}
        for font in self.fonts(page):
            # The first resource name the page lists a font under, in MuPDF's order.
            if not font.in_form:
                named.setdefault(font.xref, font.resource)
        for xref in sorted(xrefs - named.keys()):
            resource = self._free_name(page, f"C{xref}")
            self.file.restore_font(page, resource, xref)
            self._changed_page(page).keep_named(resource, xref)
            named[xref] = resource
        return named

    def _free_name(self, page: int, name: str) -> str:
        """`name`, or `name` numbered past any resource name the page already uses."""
        taken = {font.resource for font in self.fonts(page)}
        candidates = chain([name], (f"{name}{n}" for n in count(2)))
        return next(candidate for candidate in candidates if candidate not in taken)

    def write_text(
        self,
        page: int,
        *,
        runs: Sequence[TextRun],
        size: float,
        color: tuple[float, float, float],
        opacity: float,
        scale_x: float,
        turn_ccw: QuarterTurn,
    ) -> None:
        """Write each run from its origin, in its font, on top of the page, in order.

        `scale_x` narrows each run from its own start; an `opacity` of 1 is solid.
        `turn_ccw` turns each run counter-clockwise about its origin.
        """
        shape = self.doc[page].new_shape()
        for run in runs:
            at = pymupdf.Point(*run.origin)
            shape.insert_text(
                at,
                run.text,
                fontname=run.resource,
                fontsize=size,
                color=color,
                fill_opacity=opacity,
                rotate=turn_ccw,
                morph=(at, pymupdf.Matrix(scale_x, 1)),
            )
        # No letter written (empty new text): nothing to add.
        if not shape.text_cont:
            return
        # Not by the shape's commit, which reads the whole page.
        self._add_content(page, shape.text_cont.encode())

    def keep_pages(self, pages: list[int]) -> None:
        """Keep only `pages`, in that order; links, bookmarks and fields on the rest go too."""
        self.doc.select(pages)

    def save(self, path: str) -> None:
        """Write the document to `path`, as small as MuPDF makes it."""
        # Object streams compress the plain objects too: a face's width list is most of it.
        self.doc.save(path, garbage=GARBAGE_COLLECT, deflate=True, use_objstms=True)

    def close(self) -> None:
        """Release the open document."""
        self.doc.close()

    def replace_font_file(self, xref: int, font_file: bytes) -> None:
        """Swap in a new file for font `xref`. It must keep each glyph at its old number."""
        owner = self._describing_font(xref)
        # Its inner font in place, or nowhere: MuPDF never adds one so, so it's someone else's.
        if owner is None:
            raise DriverError(
                self._why_kept_whole(xref), debug=f"font {xref} has no inner font of its own"
            )
        # A TrueType face is stored as FontFile2, an OpenType one (Latin Modern) as FontFile3.
        stored = (self.doc.xref_get_key(owner, f"FontDescriptor/{key}") for key in _FONT_FILES)
        value = next((value for value_type, value in stored if value_type == "xref"), None)
        # No file where MuPDF stores ours: someone else's font.
        if value is None:
            raise DriverError(
                self._why_kept_whole(xref), debug=f"font {xref} has no file to swap"
            )
        file_xref = _object_number(value)
        self.doc.update_stream(file_xref, font_file)
        # Its size before compression (Length1), which MuPDF states for either kind.
        self.doc.xref_set_key(file_xref, "Length1", str(len(font_file)))

    def _why_kept_whole(self, xref: int) -> Message:
        """Why font `xref` stays whole in the saved file, naming it as the file does."""
        _value_type, base_font = self.doc.xref_get_key(xref, "BaseFont")
        return Message("face_not_trimmed", {"font": base_font.lstrip("/")})

    def has_tags(self) -> bool:
        """Whether the file is tagged: it has the reading order a screen reader follows."""
        catalog = self.doc.pdf_catalog()  # the file's root entry, where the tags are listed
        value_type, _value = self.doc.xref_get_key(xref=catalog, key=_TAGS_KEY)
        return value_type != _PDF_NULL

    def drop_tags(self) -> None:
        """Remove the file's tags. Saving then drops every page only they pointed at."""
        catalog = self.doc.pdf_catalog()  # the file's root entry, where the tags are listed
        self.doc.xref_set_key(xref=catalog, key=_TAGS_KEY, value=_PDF_NULL)

    def _add_content(self, page: int, stream: bytes) -> None:
        """Draw `stream` on top of everything on the page.

        The page's own drawing is wrapped first, so its settings (color,
        position) can't leak into ours.
        """
        self._changed_page(page).wrap(self.doc[page])
        self.file.add_drawing(page, stream)


@dataclass(slots=True)
class _ChangedPage:
    """What the driver did to one page, for its later changes to know."""

    # Each font it named on the page, by resource name, for erase_text to keep.
    named: dict[str, int] = field(default_factory=dict)
    # Whether the page's own drawing is known to put back each setting it changes.
    balanced: bool = False

    def keep_named(self, resource: str, xref: int) -> None:
        """Note that the page draws with font `xref` as `resource`, so an erase keeps it."""
        self.named[resource] = xref

    def wrap(self, pdf_page: pymupdf.Page) -> None:
        """Wrap the page's own drawing, the first time, so its settings can't leak into ours."""
        # Checked once, as that reads the whole page; nothing the driver does unbalances it.
        if self.balanced:
            return
        if not pdf_page.is_wrapped:
            pdf_page.wrap_contents()
        self.balanced = True


@dataclass(frozen=True, slots=True)
class _Letter:
    """One letter as get_text("rawdict") reads it, with its line's direction."""

    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1, from ascender to descender
    origin: tuple[float, float]  # where it starts, on its baseline
    direction: tuple[float, float]  # the way its line reads: (1, 0) is left to right


# A letter's middle, down then across, and its place in reading order. A plain tuple:
# it sorts at C speed, so a page read for one edit costs about what a scan of it did.
type _Middle = tuple[float, float, int]


@dataclass(frozen=True, slots=True, eq=False)
class _PageLetters:
    """A page's letters, found by where their middle is. Made by `_page_letters`."""

    in_order: list[_Letter] = field(repr=False)  # in reading order
    # Each letter's middle, top first: those level with a box are one slice, found by halving.
    middles_top_down: list[_Middle] = field(repr=False)

    def inside(self, box: Rect) -> list[_Letter]:
        """The letters whose middle is inside `box`, in reading order."""
        # The lowest key at the box's top and the highest at its bottom, so both edges count.
        first = bisect_left(self.middles_top_down, (box.y0, -math.inf, -math.inf))
        past = bisect_right(self.middles_top_down, (box.y1, math.inf, math.inf))
        across = self.middles_top_down[first:past]
        places = sorted(place for _down, x, place in across if box.x0 <= x <= box.x1)
        return [self.in_order[place] for place in places]


def _page_letters(letters: list[_Letter]) -> _PageLetters:
    """`letters`, in reading order, sorted by where their middle is too."""
    middles = (_middle_of(letter.box) for letter in letters)
    # A middle that isn't a number is inside no box, and would leave the order unsorted.
    numbers = [
        (y, x, place)
        for place, (x, y) in enumerate(middles)
        if not (math.isnan(x) or math.isnan(y))
    ]
    return _PageLetters(letters, sorted(numbers))


def _each_line(blocks: list[dict]) -> Iterator[dict]:
    """Every line of text get_text read, in reading order."""
    for block in blocks:
        yield from block["lines"]


def _each_letter(blocks: list[dict]) -> Iterator[_Letter]:
    """Every letter get_text("rawdict") read, in reading order."""
    for line in _each_line(blocks):
        direction = (line["dir"][0], line["dir"][1])
        for piece in line["spans"]:
            for char in piece["chars"]:
                origin = (char["origin"][0], char["origin"][1])
                yield _Letter(char["c"], tuple(char["bbox"]), origin, direction)


def _full_names(listed: list[str]) -> dict[str, str]:
    """Each name the text reads for a font of `listed`, cut short, to the one it was cut from.

    MuPDF keeps a name's first bytes and PyMuPDF drops its subset prefix; a cut two
    names share stays as it is, since nothing tells them apart.
    """
    by_cut: dict[str, set[str]] = {}
    for name in listed:
        by_cut.setdefault(_without_prefix(_kept_of(name)), set()).add(_without_prefix(name))
    return {cut: full for cut, (full, *others) in by_cut.items() if not others}


def _kept_of(name: str) -> str:
    """`name` as MuPDF keeps it for the text it reads: its first bytes, whole letters only."""
    return name.encode()[:_NAME_BYTES_KEPT].decode(errors="ignore")


def _without_prefix(name: str) -> str:
    """`name` without what comes up to its first "+", as PyMuPDF names a piece's font."""
    return name.partition("+")[2] or name


def _text_piece(
    raw: dict, *, direction: tuple[float, float], full_names: dict[str, str]
) -> TextPiece:
    """One piece of text from get_text("dict"), its font's name whole, on a `direction` line."""
    return TextPiece(
        text=raw["text"],
        # .get: a font the page doesn't list, such as one only a form's appearance draws.
        font=full_names.get(raw["font"], raw["font"]),
        size=raw["size"],
        color=_rgb(raw["color"]),
        opacity=raw["alpha"] / _BYTE_MAX,
        box=Rect(*raw["bbox"]),
        origin=(raw["origin"][0], raw["origin"][1]),
        direction=(direction[0], direction[1]),
    )


def _shows_text(kind: str, value: object) -> bool:
    """Whether a form field of `kind`, as PyMuPDF names it, shows `value` as text."""
    return kind in _TEXT_FIELD_KINDS and isinstance(value, str) and bool(value.strip())


def _object_number(reference: str) -> int:
    """The object a reference points at: "7 0 R" -> 7."""
    return int(reference.split()[0])


def _ink_of(drawing: dict[str, Any]) -> float:
    """How far a path's ink reaches past its outline: half its stroke, if it's stroked."""
    # A fill alone, or a hairline, which MuPDF draws as thin as it can.
    if "s" not in drawing["type"] or not drawing["width"]:
        return 0.0
    return drawing["width"] / 2


def _box_of(piece: tuple[Any, ...], *, ink: float) -> Rect:
    """The box around one piece of a path's ink: its outline, grown by `ink` all round."""
    x0, y0, x1, y1 = _outline_of(piece)
    return Rect(x0 - ink, y0 - ink, x1 + ink, y1 + ink)


def _outline_of(piece: tuple[Any, ...]) -> tuple[float, float, float, float]:
    """The box around one piece of a path, as get_drawings lists it: a line, a curve, a box."""
    kind, *shape = piece
    # A rectangle: its own box.
    if kind == "re":
        return tuple(shape[0])
    # A four-sided shape: the box around it.
    if kind == "qu":
        return tuple(shape[0].rect)
    # A line or a curve: the box around its points.
    xs, ys = [point.x for point in shape], [point.y for point in shape]
    return min(xs), min(ys), max(xs), max(ys)


def _middle_of(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """The middle of a box given as x0, y0, x1, y1."""
    x0, y0, x1, y1 = bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def _strip_along(letters: list[_Letter]) -> Rect | None:
    """A thin box along `letters`, just above their baselines; None when there are none."""
    points = [point for letter in letters for point in _lifted(letter)]
    if not points:
        return None
    xs, ys = [x for x, _y in points], [y for _x, y in points]
    pad = _STRIP_PAD_PT
    return Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _lifted(letter: _Letter) -> list[tuple[float, float]]:
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


def _written_name(resource: str) -> str:
    """The resource name `resource` as a page's drawing writes it: "/", then each byte, escaped.

    PyMuPDF hands names back decoded ("/F#201" as "F 1"), a byte that isn't
    UTF-8 as a stand-in character (a surrogate), so the bytes come back exactly.
    """
    raw = resource.encode("utf-8", "surrogateescape")
    plain = (chr(byte) if _is_plain(byte) else f"#{byte:02X}" for byte in raw)
    return "/" + "".join(plain)


def _is_plain(byte: int) -> bool:
    """Whether a PDF name can hold `byte` as it is."""
    return byte in _PRINTABLE and byte not in _ENDS_OR_ESCAPES_A_NAME


def _cut(drawn: pymupdf.Pixmap, box: pymupdf.IRect) -> bytes:
    """The pixels of `drawn` inside `box`, as a PNG; `box` is in `drawn`'s device pixels."""
    part = pymupdf.Pixmap(drawn.colorspace, box & drawn.irect, drawn.alpha)
    part.copy(drawn, part.irect)
    return part.tobytes("png")


def _rgb(packed: int) -> tuple[float, float, float]:
    """A 0xRRGGBB color as r, g, b, each 0-1."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
