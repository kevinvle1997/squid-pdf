"""The one driver there is: PyMuPDF.

Only primitives here (see `core.pdf.driver`): the hard-to-read MuPDF calls come
from `core.pdf.lowlevel`, whose `PdfFile` the driver holds, and the everyday ones are below.
Only `core.engine` opens the driver: nothing outside `core` learns that MuPDF is underneath.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache, partial
from itertools import chain, count
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
    PDF_NULL,
    PdfFile,
    pdf_file,
)
from squidpdf.core.pdf.png import png_of
from squidpdf.core.types import (
    QUARTER_TURNS,
    SOLID,
    CodeRun,
    DrawnGlyph,
    Face,
    FontCode,
    FontDescriptor,
    FontFileType,
    FontKind,
    FontResource,
    FormField,
    GlyphId,
    HiddenCopy,
    Page,
    PageFont,
    QuarterTurn,
    Rect,
    TextPiece,
    TextRun,
)

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel
# A narrowing matrix as PyMuPDF writes one: to 5 decimals, and a number nearer 0 than 1e-4 as 0.
_MATRIX_DP = 5
_NEAR_ZERO = 1e-4
_NOT_NARROWED = (
    "1 0 0 1 0 0 cm\n"  # the narrowing of a run at its own width, which moves nothing
)
_BYTE_MAX = 255  # the top of one color channel in 0xRRGGBB
# The bytes a PDF name holds as they are: printable, and none that ends a name or
# starts an escape. Any other is written #xx.
_PRINTABLE = range(0x21, 0x7F)
_ENDS_OR_ESCAPES_A_NAME = b"()<>[]{}/%#"

# Where a font description keeps the font file, by the kind of file it is.
_FONT_FILES = ("FontFile2", "FontFile3")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
_NAME_CUT_AT = 31  # bytes of a font's name MuPDF keeps for the text it reads
_PREFIX_PLUS_AT = 6  # where PyMuPDF looks for the "+" that ends a subset prefix

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
        return self.glyph_of(ch) != 0

    def glyph_of(self, ch: str) -> GlyphId:
        """The glyph that draws `ch`; 0, the shape for a missing letter, when it has none."""
        return GlyphId(self.font.has_glyph(ord(ch)))

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points: what MuPDF's `text_length` says, quicker."""
        return sum(map(self.width_in_ems, map(ord, text))) * size


def _mupdf_font(font_file: bytes) -> _MuPDFFont:
    """`font_file` opened in MuPDF, to measure with. Raises MuPDF's error if it isn't a font."""
    font = pymupdf.Font(fontbuffer=font_file)
    return _MuPDFFont(font, cache(font.glyph_advance))


def open_face(font_file: bytes) -> FontProgram:
    """A face we ship, opened from its file anew: kept by nobody once its reader is done."""
    return _mupdf_font(font_file)


@cache
def _kept_face(face: Face) -> FontProgram:
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
    return _MuPDFDriver(doc, pdf_file(doc))


@dataclass(frozen=True, slots=True, eq=False)
class _MuPDFDriver:
    """A PDF open in MuPDF. Implements `core.pdf.driver.PdfDriver`. Made by `open_driver`."""

    doc: pymupdf.Document
    file: PdfFile  # the same document, for the calls MuPDF's low-level API makes
    # What this driver did to each page, by its object, whose number survives renumbering.
    changed: dict[int, _ChangedPage] = field(default_factory=dict, repr=False)
    # Each font `write_text` wrote in, opened from the file it stores, by its object: a few.
    programs: dict[int, _MuPDFFont] = field(default_factory=dict, repr=False)

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
        return png_of(_drawn(self.doc[page], scale))

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]:
        """Each box of the page unrotated as a PNG, `scale` pixels per point.

        Cut from the whole page, drawn as `page_image` draws it: a clip changes how MuPDF
        draws pictures, shadings and the paths it cuts, which would show a seam.
        """
        drawn = _drawn(self.doc[page], scale)
        to_pixels = pymupdf.Matrix(scale, scale)
        return [
            _cut(drawn, (pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) * to_pixels).irect)
            for box in boxes
        ]

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Each line of text on the page, split into the pieces it is drawn in.

        MuPDF's text names a piece's font only by name, so the font is found by the name its
        text reads: as the page lists it, else as its file names itself.
        """
        blocks = self.doc[page].get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        fonts = self.fonts(page)
        by_name = _PageTextFonts(_by_listed_name(fonts))
        named_inside = partial(self._by_name_inside, fonts)
        return [
            [
                _text_piece(
                    raw, direction=line["dir"], font=by_name.font_of(raw["font"], named_inside)
                )
                for raw in line["spans"]
            ]
            for line in _each_line(blocks)
        ]

    def _by_name_inside(self, fonts: list[PageFont]) -> dict[str, PageFont]:
        """The stored ones of `fonts` by the name inside each file, as their text reads it."""
        by_name: dict[str, PageFont] = {}
        # The library's order: the first by a name wins.
        for font in fonts:
            inside = self.file.fonts.text_font_name(font.xref) if font.is_embedded else None
            if inside is not None:
                by_name.setdefault(_as_pymupdf_names(inside), font)
        return by_name

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]:
        """The letters inside each box on the page, in reading order.

        A letter counts when its middle is inside, so one that grazes the edge doesn't.
        """
        letters = self._letters(page, near=boxes)
        return ["".join(letter.text for letter in letters.inside(box)) for box in boxes]

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]:
        """Each letter the page draws, and the shape its code picked, in the order drawn."""
        return [
            DrawnGlyph(chr(letter), GlyphId(glyph), (origin[0], origin[1]), box[2] - box[0])
            for run in self.doc[page].get_texttrace()
            for letter, glyph, origin, box in run["chars"]
        ]

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

    def _letters(self, page: int, *, near: list[Rect]) -> _PageLetters:
        """The letters of each line on the page whose box meets one of `near`, to find by place.

        A line's box holds each of its letters' boxes, so a line left out has none inside them.
        """
        blocks = self.doc[page].get_text("rawdict", flags=_TEXT_FLAGS)["blocks"]
        lines = (line for line in _each_line(blocks) if _meets_any(line["bbox"], near))
        return _page_letters(list(_each_letter(lines)))

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
        name_type, name = self.doc.xref_get_key(owner, "FontDescriptor/FontName")
        return FontDescriptor(
            flags=0 if flags is None else int(flags),
            weight=self._number(owner, "FontDescriptor/FontWeight"),
            italic_angle=0.0 if angle is None else angle,
            name=name.removeprefix("/") if name_type == "name" else None,
        )

    def _describing_font(self, xref: int) -> int | None:
        """Where the font's description lives: the font itself, or a Type0's inner font.

        None when a Type0's inner font isn't an object of its own in the file.
        """
        value_type, _value = self.doc.xref_get_key(xref, "DescendantFonts")
        # A simple font describes itself.
        if value_type not in ("array", "xref"):
            return xref
        return self.file.fonts.inner_font(xref)

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
        return self.file.fonts.font_codes(xref, code_bytes)

    def listed_widths(self, xref: int) -> dict[str, float] | None:
        """Each letter the font's width list gives a width, per 1000 em; None without a list.

        Read from MuPDF's own record of the font. Raises DriverError when MuPDF
        can't load it.
        """
        return self.file.fonts.listed_widths(xref)

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
        return _kept_face(face)

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
        letters = self._letters(page, near=boxes)
        with self.file.redaction.links_kept(page):
            # No letter's middle inside: erase the whole box, as nothing else would.
            self.file.redaction.redact(
                page, [_strip_along(letters.inside(box)) or box for box in boxes]
            )
            left = self.text_in(page, boxes)
            missed = [box for box, text in zip(boxes, left, strict=True) if text.strip()]
            # Read again only after a second erase: most boxes are clear after the first.
            if missed:
                self.file.redaction.redact(page, missed)
                left = self.text_in(page, boxes)
        for resource, xref in self._changed_page(page).named.items():
            self.file.additions.restore_font(page, resource, xref)
        return left

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`, whatever it does."""
        self.file.redaction.drop_links(page, boxes)

    def hidden_copies(self, page: int) -> list[str]:
        """Every hidden copy on the page, as text, and each string MuPDF may miss."""
        return self.file.page_hidden_copies.read(page)

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(hidden_copy)` for each hidden copy on the page; one blanked goes."""
        self.file.page_hidden_copies.rewrite(page, rewritten)

    def document_hidden_copies(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        """Each of the document's own hidden copies that `holding` is true of, and where."""
        return self.file.document_hidden_copies.read(holding)

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(copy)` for the document's hidden copies; one left blank goes."""
        self.file.document_hidden_copies.rewrite(rewritten)

    def is_signed(self) -> bool:
        """Whether the document holds a signature, or anything `drop_signatures` deletes."""
        return self.file.signatures.is_signed()

    def drop_signatures(self) -> None:
        """Delete every signature, and what the file keeps only to check one."""
        self.file.signatures.drop()

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
        letter_spacing: float,
    ) -> None:
        """Write each run's codes in its font, from `origin` on, on top of the page.

        One text object: each run switches to its font, and the pen moves on by
        that font's widths. PyMuPDF's writers only take letters, so it's written
        out here.
        """
        x, y = self.file.additions.to_pdf_space(page, origin)
        resources = self._resources_of(page, {run.xref for run in runs})
        shown = " ".join(
            f"{_written_name(resources[run.xref])} {size:.{_PDF_DP}f} Tf <{run.codes.hex()}> Tj"
            for run in runs
        )
        # See-through: a graphics state that paints at `opacity`.
        paint = (
            f" /{self.file.additions.add_opacity(page, opacity)} gs" if opacity < SOLID else ""
        )
        # Where the text goes: narrowed along its line, turned, and placed.
        cos, sin = QUARTER_TURNS[turn_ccw]
        matrix = [scale_x * cos, scale_x * sin, -sin, cos, x, y]
        placed = " ".join(f"{number:.{_PDF_DP}f}" for number in matrix)
        color_operands = " ".join(f"{channel:.{_PDF_DP}f}" for channel in color)
        # Save the page's settings, set color, place the text, write each run in
        # its font and size, then put the settings back.
        spaced = _written_spacing(letter_spacing)
        stream = f"q{paint} BT {color_operands} rg {placed} Tm{spaced} {shown} ET Q"
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
            self.file.additions.restore_font(page, resource, xref)
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
        letter_spacing: float,
    ) -> None:
        """Write each run from its origin, in its font, on top of the page, in order.

        Each run's font is one `add_font` added. `scale_x` narrows a run from its own start, and
        `turn_ccw` turns it about its origin. Written to the byte as PyMuPDF's `insert_text`
        writes it, but for `letter_spacing`.
        """
        written = [run for run in runs if run.text]
        # Empty new text.
        if not written:
            return
        pdf_page = self.doc[page]
        # Where the page's own coordinates start in the PDF's: the crop box's corner, down
        # from the paper's top. Not `to_pdf_space`, whose numbers differ in the last bit.
        paper_top, corner = pdf_page.mediabox.y1, pdf_page.cropbox_position
        xrefs = self._changed_page(page).named
        style = _RunStyle(
            paint=self._paint_at(page, opacity),
            scale_x=scale_x,
            turn_ccw=turn_ccw,
            setting=_written_setting(size, color, letter_spacing),
        )
        stream = ""
        for run in written:
            x, y = run.origin
            start = (x + corner.x, paper_top - y - corner.y)
            glyphs = self._glyph_codes(xrefs[run.resource], run.text)
            stream += _written_run(start, glyphs=glyphs, resource=run.resource, style=style)
        self._add_content(page, stream.encode())

    def _paint_at(self, page: int, opacity: float) -> str:
        """The graphics state that paints at `opacity`, as a drawing sets it; none if solid."""
        # Solid: the page's own state paints it.
        if opacity >= SOLID:
            return ""
        return f"/{self.file.additions.add_opacity_as_pymupdf(page, opacity)} gs\n"

    def _glyph_codes(self, xref: int, text: str) -> str:
        """`text` as codes in font `xref`, four hex digits a letter: each its glyph's number.

        `add_font` adds a font whose codes are its glyphs' numbers.
        """
        program = self._program_of(xref)
        return "".join(f"{program.glyph_of(ch):04x}" for ch in text)

    def _program_of(self, xref: int) -> _MuPDFFont:
        """Font `xref`'s file, opened to look its letters up in; opened once."""
        if xref not in self.programs:
            self.programs[xref] = _mupdf_font(self.font_bytes(xref))
        return self.programs[xref]

    def keep_pages(self, pages: list[int]) -> None:
        """Keep only `pages`, in that order; links, bookmarks and fields on the rest go too."""
        self.doc.select(pages)

    def turn_page(self, page: int, turn_cw: QuarterTurn) -> None:
        """Set the turn `page` asks for, clockwise, in place of the one it had."""
        self.doc[page].set_rotation(turn_cw)

    def drop_active_content(self) -> bool:
        """Delete what acts on its own or reaches outside the file; whether there was any."""
        return self.file.active_content.drop()

    def mark_changed_by(self, producer: str, when: datetime) -> None:
        """Say in the file's metadata that `producer` changed it, at `when`."""
        self.file.metadata.mark_changed_by(producer, when)

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
        return value_type != PDF_NULL

    def drop_tags(self) -> None:
        """Remove the file's tags. Saving then drops every page only they pointed at."""
        catalog = self.doc.pdf_catalog()  # the file's root entry, where the tags are listed
        self.doc.xref_set_key(xref=catalog, key=_TAGS_KEY, value=PDF_NULL)

    def _add_content(self, page: int, stream: bytes) -> None:
        """Draw `stream` on top of everything on the page.

        The page's own drawing is wrapped first, so its settings (color,
        position) can't leak into ours.
        """
        self._changed_page(page).wrap(self.doc[page])
        self.file.additions.add_drawing(page, stream)


@dataclass(slots=True)
class _ChangedPage:
    """What the driver did to one page, for its later changes to know."""

    # Each font it named, by resource name: those write_text writes in and erase_text keeps.
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
    """Letters on a page, found by where their middle is. Made by `_page_letters`."""

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


def _each_letter(lines: Iterable[dict]) -> Iterator[_Letter]:
    """Every letter of `lines`, as get_text("rawdict") read them, in reading order."""
    for line in lines:
        direction = (line["dir"][0], line["dir"][1])
        for piece in line["spans"]:
            for char in piece["chars"]:
                origin = (char["origin"][0], char["origin"][1])
                yield _Letter(char["c"], tuple(char["bbox"]), origin, direction)


@dataclass(slots=True)
class _PageTextFonts:
    """A page's fonts by the name their text reads, to find the one that draws a piece."""

    listed: dict[str, PageFont]  # each by the name the page lists it under, as text reads that
    # Each stored one by the name inside its file: None until a piece reads a name not listed.
    inside: dict[str, PageFont] | None = None

    def font_of(
        self, read_as: str, named_inside: Callable[[], dict[str, PageFont]]
    ) -> PageFont | None:
        """The font whose text reads `read_as`, as listed or as named inside; None if none."""
        # .get: a name the page doesn't list, as the one a font's own file gives.
        listed = self.listed.get(read_as)
        if listed is not None:
            return listed
        if self.inside is None:
            self.inside = named_inside()
        # .get: a font the page doesn't list, such as one only a form field's own drawing uses.
        return self.inside.get(read_as)


def _by_listed_name(fonts: list[PageFont]) -> dict[str, PageFont]:
    """`fonts` by the name each is listed under, as text reads it: cut, no subset prefix."""
    by_name: dict[str, PageFont] = {}
    # The library's order: of two whose text reads one name, as cut alike, the first wins.
    for font in fonts:
        by_name.setdefault(_as_pymupdf_names(_cut_of(font.name)), font)
    return by_name


def _whole_name(read_as: str, font: PageFont | None) -> str:
    """The name a piece's font is read as, whole where that's the page's listing of it, cut."""
    # A font the page doesn't list, or one its text reads by the name inside its file.
    if font is None or _as_pymupdf_names(_cut_of(font.name)) != read_as:
        return read_as
    return _as_pymupdf_names(font.name)


def _cut_of(name: str) -> str:
    """`name` as MuPDF keeps it for the text it reads; PyMuPDF reads each byte as a letter."""
    return name[:_NAME_CUT_AT]


def _as_pymupdf_names(name: str) -> str:
    """`name` as PyMuPDF names a piece's font: without a subset prefix it finds.

    Its own rule, not `strip_subset`'s: a "+" anywhere else stays.
    """
    has_prefix = name[_PREFIX_PLUS_AT : _PREFIX_PLUS_AT + 1] == "+"
    return name[_PREFIX_PLUS_AT + 1 :] if has_prefix else name


def _text_piece(
    raw: dict, *, direction: tuple[float, float], font: PageFont | None
) -> TextPiece:
    """One piece of text from get_text("dict"), drawn in `font`, on a `direction` line.

    `font` is None for a font the page doesn't list.
    """
    return TextPiece(
        text=raw["text"],
        font=_whole_name(raw["font"], font),
        font_object=None if font is None else font.xref,
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


def _meets_any(line_box: tuple[float, float, float, float], boxes: list[Rect]) -> bool:
    """Whether a line's box meets one of `boxes`, edges touching; always, if it's no box."""
    x0, y0, x1, y1 = line_box
    is_box = x0 <= x1 and y0 <= y1
    # Inside out, or not a number: it bounds nothing, so it's read.
    if not is_box:
        return True
    return any(_meets(line_box, box) for box in boxes)


def _meets(line_box: tuple[float, float, float, float], box: Rect) -> bool:
    """Whether a line's box meets `box`, edges touching."""
    x0, y0, x1, y1 = line_box
    return box.x0 <= x1 and x0 <= box.x1 and box.y0 <= y1 and y0 <= box.y1


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


def _written_spacing(letter_spacing: float) -> str:
    """Letter spacing as a drawing sets it, after a space; nothing when it's none."""
    return f" {letter_spacing:.{_PDF_DP}f} Tc" if letter_spacing else ""


def _written_narrowing(start: tuple[float, float], scale_x: float) -> str:
    """The matrix narrowing a run by `scale_x` toward its start, as a drawing writes it.

    Worked out in MuPDF's matrices and rounded as PyMuPDF does: the same numbers, to the bit.
    """
    # Not narrowed: the same numbers wherever it starts.
    if scale_x == 1:
        return _NOT_NARROWED
    shift = pymupdf.Matrix(1, 0, 0, 1, *start)
    narrowed = ~shift * pymupdf.Matrix(scale_x, 1) * shift
    return pymupdf.format_g([_matrix_number(number) for number in narrowed]) + " cm\n"


def _matrix_number(number: float) -> float:
    """One of a matrix's numbers, rounded as PyMuPDF rounds a matrix `insert_text` writes."""
    return round(number, _MATRIX_DP) if abs(number) >= _NEAR_ZERO else 0


@dataclass(frozen=True, slots=True)
class _RunStyle:
    """What every run of one `write_text` is written with."""

    paint: str  # the graphics state that paints it see-through, or nothing when solid
    scale_x: float
    turn_ccw: QuarterTurn
    setting: str  # its size, colour and letter spacing, as a drawing writes them


def _written_setting(
    size: float, color: tuple[float, float, float], letter_spacing: float
) -> str:
    """A run's size, and its colour for strokes and fills alike, as PyMuPDF writes them."""
    # Each number as PyMuPDF writes one (format_g): the fewest digits its float needs.
    rgb = pymupdf.format_g(color)
    return f"{pymupdf.format_g(size)} Tf {rgb} RG {rgb} rg{_written_spacing(letter_spacing)}"


def _written_run(
    start: tuple[float, float], *, glyphs: str, resource: str, style: _RunStyle
) -> str:
    """One run, written from `start` in the PDF's coordinates: its glyphs in `resource`."""
    placed = pymupdf.format_g(_turned_back(start, style.turn_ccw))
    narrowed = _written_narrowing(start, style.scale_x)
    # Save the page's settings, paint, narrow and turn about the run's start, place it,
    # write its glyphs in its font, size and colour, then put the settings back.
    return (
        f"\nq\n{style.paint}BT\n{narrowed}{_written_turn(style.turn_ccw)}"
        f"1 0 0 1 {placed} Tm\n{_written_name(resource)} {style.setting} [<{glyphs}>]TJ"
        "\nET\nQ\n"
    )


def _written_turn(turn_ccw: QuarterTurn) -> str:
    """The matrix turning a run `turn_ccw` counter-clockwise, as a drawing writes it."""
    if turn_ccw == 0:
        return ""
    cos, sin = QUARTER_TURNS[turn_ccw]
    return f"{cos} {sin} {-sin} {cos} 0 0 cm\n"


def _turned_back(point: tuple[float, float], turn_ccw: QuarterTurn) -> tuple[float, float]:
    """`point` turned `turn_ccw` clockwise: where it is once the page turns the other way."""
    x, y = point
    cos, sin = QUARTER_TURNS[turn_ccw]
    return cos * x + sin * y, cos * y - sin * x


def _drawn(pdf_page: pymupdf.Page, scale: float) -> pymupdf.Pixmap:
    """The whole page unrotated, `scale` pixels per point, drawn with no clip."""
    matrix = pdf_page.derotation_matrix * pymupdf.Matrix(scale, scale)
    return pdf_page.get_pixmap(matrix=matrix, alpha=False)


def _cut(drawn: pymupdf.Pixmap, box: pymupdf.IRect) -> bytes:
    """The pixels of `drawn` inside `box`, as a PNG; `box` is in `drawn`'s device pixels."""
    part = pymupdf.Pixmap(drawn.colorspace, box & drawn.irect, drawn.alpha)
    part.copy(drawn, part.irect)
    return png_of(part)


def _rgb(packed: int) -> tuple[float, float, float]:
    """A 0xRRGGBB color as r, g, b, each 0-1."""
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
