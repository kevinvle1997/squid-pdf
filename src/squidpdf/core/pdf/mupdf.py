"""Only `core.engine` opens the driver: nothing outside `core` learns MuPDF is underneath."""

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

import squidpdf.core.constants as constants
from squidpdf.core.app.errors import Damaged, Encrypted, Failure, TooHeavy, machine_failure
from squidpdf.core.app.message import Message
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.pdf.lowlevel import (
    MUPDF_ERRORS,
    MUPDF_OWN_ERRORS,
    MUPDF_SYSTEM_ERRORS,
    MUPDF_TOO_HEAVY,
    PDF_NULL,
    PDF_REFERENCE,
    PdfFile,
    object_number,
    pdf_file,
)
from squidpdf.core.pdf.png import png_of
from squidpdf.core.pdf.text_fonts import PageTextFonts, as_pymupdf_names, page_text_fonts
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
    PlacedLetter,
    QuarterTurn,
    Rect,
    TextPiece,
    TextRun,
)

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel
# As PyMuPDF writes a narrowing matrix: to 5 decimals, and nearer 0 than 1e-4 as 0.
_MATRIX_DP = 5
_NEAR_ZERO = 1e-4
_NOT_NARROWED = "1 0 0 1 0 0 cm\n"
_BYTE_MAX = 255
_KEEP_PROTECTION = 0  # pymupdf.PDF_ENCRYPT_KEEP, which its type stubs leave out
_TRAILER = -1
# Both parts empty: MuPDF keeps the first part as it is and writes the second.
_EMPTY_ID = "[<> <>]"
_FALSE = ("bool", "false")
_CRYPT_FILTERS_FROM = (
    4  # the first protection version (V) with crypt filters, and EncryptMetadata
)
_PRINTABLE = range(0x21, 0x7F)
_ENDS_OR_ESCAPES_A_NAME = b"()<>[]{}/%#"

_FONT_FILES = ("FontFile2", "FontFile3")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
_DRAWN_FLAGS = _TEXT_FLAGS | pymupdf.TEXT_IGNORE_ACTUALTEXT

_KINDS: dict[str, FontKind] = {
    "TrueType": "truetype",
    "Type0": "type0",
    "Type1": "type1",
    "MMType1": "type1",
    "Type3": "type3",
}
_IN_PLACE = 0
_UNNAMED_TYPE3 = "Type3 ({font_object} 0 R)"
_UNNAMED_TYPE3_IN_PLACE = "Type3 (no name)"
_FILE_TYPES: dict[str, FontFileType] = {
    "ttf": "truetype",
    "otf": "opentype",
    "cff": "cff",
    "cid": "cff",
    "pfa": "type1",
}
_IDENTITY_ACROSS = "Identity-H"

_PICTURE_KINDS = ("fill-image", "fill-imgmask", "fill-shade")

_TEXT_FIELD_KINDS = ("Text", "ComboBox", "ListBox")

_STRIP_PAD_PT = 0.1
# Every font's glyph boxes reach just above the baseline; few reach the next line's.
_STRIP_LIFTS = (0.05, 0.2)

_TAGS_KEY = "StructTreeRoot"

# The first match wins, and MuPDF's own errors all derive from one: narrower ones first.
MUPDF_FAILURES = (
    Failure(raised=MUPDF_TOO_HEAVY, problem=TooHeavy),
    Failure(raised=MUPDF_SYSTEM_ERRORS, problem=machine_failure),
    Failure(raised=MUPDF_OWN_ERRORS, problem=Damaged),
)

DRIVER_BUILD = f"mupdf-{pymupdf.mupdf_version}"


@dataclass(frozen=True, slots=True, eq=False)
class _MuPDFFont:
    font: pymupdf.Font
    # Read once: a fit check measures the same few letters thousands of times.
    width_in_ems: Callable[[int], float] = field(repr=False)

    def listed_letters(self) -> list[int]:
        return list(self.font.valid_codepoints())

    def advance(self, ch: str) -> float:
        return self.width_in_ems(ord(ch))

    def maps(self, ch: str) -> bool:
        return self.glyph_of(ch) != 0

    def glyph_of(self, ch: str) -> GlyphId:
        return GlyphId(self.font.has_glyph(ord(ch)))

    def width(self, text: str, size: float) -> float:
        return sum(map(self.width_in_ems, map(ord, text))) * size


def _mupdf_font(font_file: bytes) -> _MuPDFFont:
    font = pymupdf.Font(fontbuffer=font_file)
    return _MuPDFFont(font, cache(font.glyph_advance))


def open_face(font_file: bytes) -> FontProgram:
    return _mupdf_font(font_file)


@cache
def _kept_face(face: Face) -> FontProgram:
    return _mupdf_font(face_bytes(face))


def open_driver(path: str) -> PdfDriver:
    """Raises FileNotFoundError, Encrypted behind a password, or Damaged if it isn't a PDF."""
    try:
        # Left to sniff, MuPDF opens a PNG as a document.
        doc = pymupdf.open(path, filetype="pdf")
    except pymupdf.FileNotFoundError as exc:
        raise FileNotFoundError(path) from exc
    except pymupdf.FileDataError as exc:
        raise Damaged from exc
    if doc.needs_pass:
        doc.close()
        raise Encrypted
    try:
        page_count = len(doc)
    except MUPDF_ERRORS as exc:
        doc.close()
        raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None
    if page_count == 0:
        doc.close()
        raise Damaged(debug="no pages")
    file = pdf_file(doc)
    # Before anything is added: an entry naming no object would take the first one added.
    file.protection.write_entries_in_place()
    return _MuPDFDriver(doc, file)


@dataclass(slots=True)
class _ChangedPages:
    pages: dict[int, _ChangedPage] = field(default_factory=dict)

    def page(self, page_object: int) -> _ChangedPage:
        return self.pages.setdefault(page_object, _ChangedPage())


@dataclass(slots=True)
class _OpenedPrograms:
    programs: dict[int, _MuPDFFont] = field(default_factory=dict)

    def program(self, font_object: int, make: Callable[[], _MuPDFFont]) -> _MuPDFFont:
        if font_object not in self.programs:
            self.programs[font_object] = make()
        return self.programs[font_object]


@dataclass(frozen=True, slots=True, eq=False)
class _MuPDFDriver:
    doc: pymupdf.Document
    file: PdfFile
    changed: _ChangedPages = field(default_factory=_ChangedPages, repr=False)
    programs: _OpenedPrograms = field(default_factory=_OpenedPrograms, repr=False)

    def page_count(self) -> int:
        return len(self.doc)

    def pages(self) -> list[Page]:
        try:
            pages = [self.doc[pno] for pno in range(len(self.doc))]
        except (*MUPDF_ERRORS, IndexError) as exc:
            raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None
        return [Page(p.cropbox.width, p.cropbox.height, p.rotation) for p in pages]

    def page_image(self, page: int, scale: float) -> bytes:
        return png_of(_drawn(self.doc[page], scale))

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]:
        """Cut from the whole page: a clip changes how MuPDF draws pictures, showing a seam."""
        drawn = _drawn(self.doc[page], scale)
        to_pixels = pymupdf.Matrix(scale, scale)
        return [
            _cut(drawn, (pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) * to_pixels).irect)
            for box in boxes
        ]

    def text_lines(self, page: int) -> list[list[TextPiece]]:
        """Text names a font only by name: two fonts read as one name give the first."""
        blocks = self.doc[page].get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        fonts = self.fonts(page)
        by_name = page_text_fonts(fonts)
        by_embedded_name = partial(self._by_embedded_name, fonts)
        return [
            [
                _text_piece(
                    raw,
                    direction=line["dir"],
                    font=by_name.font_of(raw["font"], by_embedded_name),
                    font_name=_font_name(raw["font"], by_name),
                )
                for raw in line["spans"]
            ]
            for line in _each_line(blocks)
        ]

    def _by_embedded_name(self, fonts: list[PageFont]) -> dict[str, PageFont]:
        by_name: dict[str, PageFont] = {}
        for font in fonts:
            embedded = (
                self.file.fonts.text_font_name(font.font_object) if font.is_embedded else None
            )
            if embedded is not None:
                by_name.setdefault(as_pymupdf_names(embedded), font)
        return by_name

    def text_in(self, page: int, boxes: list[Rect]) -> list[str]:
        letters = self._letters(page, near=boxes)
        return ["".join(letter.text for letter in letters.inside(box)) for box in boxes]

    def letters_in(self, page: int, boxes: list[Rect]) -> list[list[PlacedLetter]]:
        letters = self._letters(page, near=boxes, flags=_DRAWN_FLAGS)
        return [[_placed(letter) for letter in letters.inside(box)] for box in boxes]

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]:
        return self.file.glyphs.drawn(page)

    def drawn_glyph_boxes(self, page: int) -> list[Rect] | None:
        try:
            boxes = self.file.glyphs.boxes(page)
        except MUPDF_OWN_ERRORS:
            return None
        return [Rect(*box) for box in boxes]

    def drawn_boxes(self, page: int) -> list[Rect]:
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

    def link_boxes(self, page: int) -> list[Rect]:
        return self.file.redaction.link_boxes(page)

    def form_fields(self, page: int) -> list[FormField]:
        if not self.doc.is_form_pdf:
            return []
        return [
            FormField(Rect(*field.rect), field.field_value)
            for field in self.doc[page].widgets()
            if _shows_text(field.field_type_string, field.field_value)
        ]

    def _letters(
        self, page: int, *, near: list[Rect], flags: int = _TEXT_FLAGS
    ) -> _PageLetters:
        blocks = self.doc[page].get_text("rawdict", flags=flags)["blocks"]
        lines = (line for line in _each_line(blocks) if _meets_any(line["bbox"], near))
        return _page_letters(list(_each_letter(lines)))

    def fonts(self, page: int) -> list[PageFont]:
        return self._fonts_of(self._listed_fonts(page))

    def _listed_fonts(self, page: int) -> list[_ListedFont]:
        return [_ListedFont(*listed) for listed in self.doc[page].get_fonts(full=True)]

    def _fonts_of(self, listed: list[_ListedFont]) -> list[PageFont]:
        return [self._page_font(font) for font in listed if not font.written_in_place]

    def _page_font(self, listed: _ListedFont) -> PageFont:
        kind = listed.kind
        return PageFont(
            font_object=listed.font_object,
            name=listed.name if kind != "Type3" else self._type3_name(listed.font_object),
            kind=_KINDS.get(kind, "other"),
            file_type=_FILE_TYPES.get(listed.file_type, "none"),
            resource=listed.resource,
            codes_are_cids=listed.encoding == _IDENTITY_ACROSS,
            in_form=listed.referencer != 0,
        )

    def _type3_name(self, font_object: int) -> str:
        value_type, value = self.doc.xref_get_key(font_object, "Name")
        if value_type != "name":
            return _UNNAMED_TYPE3.format(font_object=font_object)
        return value.removeprefix("/")

    def font_bytes(self, font_object: int) -> bytes:
        try:
            _name, _ext, _kind, buffer = self.doc.extract_font(font_object)
        except MUPDF_ERRORS as exc:
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc
        if not buffer:
            raise DriverError(Message("font_unreadable"), debug=f"font {font_object} is empty")
        return buffer

    def font_descriptor(self, font_object: int) -> FontDescriptor | None:
        owner = self._describing_font(font_object)
        if owner is None:
            return None
        value_type, _value = self.doc.xref_get_key(owner, "FontDescriptor")
        if value_type == PDF_NULL:
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

    def _describing_font(self, font_object: int) -> int | None:
        value_type, _value = self.doc.xref_get_key(font_object, "DescendantFonts")
        if value_type not in ("array", PDF_REFERENCE):
            return font_object
        return self.file.fonts.inner_font(font_object)

    def _number(self, xref: int, key: str) -> float | None:
        value_type, value = self.doc.xref_get_key(xref, key)
        if value_type not in ("int", "float"):
            return None
        return float(value)

    def font_codes(self, font_object: int, code_bytes: int) -> list[FontCode] | None:
        return self.file.fonts.font_codes(font_object, code_bytes)

    def listed_widths(self, font_object: int) -> dict[str, float] | None:
        return self.file.fonts.listed_widths(font_object)

    def open_font(self, font_file: bytes) -> _MuPDFFont:
        # No bytes at all: MuPDF would open its own Noto Serif in their place.
        if not font_file:
            raise DriverError(Message("font_unreadable"), debug="no bytes")
        try:
            return _mupdf_font(font_file)
        except MUPDF_ERRORS as exc:
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc

    def face_font(self, face: Face) -> FontProgram:
        return _kept_face(face)

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """Numbered past any taken: given a name in use, MuPDF hands back the font there."""
        free_name = self._free_name(page, resource)
        pdf_page = self.doc[page]
        try:
            font_object = pdf_page.insert_font(fontname=free_name, fontbuffer=font_file)
        except MUPDF_ERRORS as exc:
            raise DriverError(Message("font_not_added"), debug=str(exc)) from exc
        self._changed_page(page).keep_named(free_name, font_object)
        return FontResource(free_name, font_object)

    def _changed_page(self, page: int) -> _ChangedPage:
        return self.changed.page(self.doc[page].xref)

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        """A thin strip above the baselines first: a letter's box can reach the lines around."""
        letters = self._letters(page, near=boxes, flags=_DRAWN_FLAGS)
        with self.file.redaction.links_kept(page):
            self.file.redaction.redact(
                page, [_strip_along(letters.inside(box)) or box for box in boxes]
            )
            left = self.text_in(page, boxes)
            missed = [box for box, text in zip(boxes, left, strict=True) if text.strip()]
            if missed:
                self.file.redaction.redact(page, missed)
                left = self.text_in(page, boxes)
        for resource, font_object in self._changed_page(page).named.items():
            self.file.additions.restore_font(page, resource, font_object)
        return left

    def drop_links(
        self, page: int, boxes: list[Rect], *, holding: Callable[[str], bool] | None = None
    ) -> None:
        self.file.redaction.drop_links(page, boxes, holding=holding)

    def hidden_copies(self, page: int) -> list[str]:
        return self.file.page_hidden_copies.read(page)

    def rewrite_hidden_copies(self, page: int, rewritten: Callable[[str], str]) -> None:
        self.file.page_hidden_copies.rewrite(page, rewritten)

    def document_hidden_copies(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        return self.file.document_hidden_copies.read(holding)

    def rewrite_document_hidden_copies(self, rewritten: Callable[[str], str]) -> None:
        self.file.document_hidden_copies.rewrite(rewritten)

    def is_signed(self) -> bool:
        return self.file.signatures.is_signed()

    def holds_signature(self) -> bool:
        return self.file.signatures.holds_signature()

    def drop_signatures(self) -> None:
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
        """PyMuPDF's writers only take letters, so it's written out here."""
        start = self.file.additions.to_pdf_space(page, origin)
        resources = self._resources_of(page, {run.font_object for run in runs})
        shown = " ".join(
            _written_codes(run, resource=resources[run.font_object], size=size) for run in runs
        )
        paint = self._codes_paint_at(page, opacity)
        placed = _written_placing(start, scale_x=scale_x, turn_ccw=turn_ccw)
        spaced = _written_spacing(letter_spacing)
        stream = f"q{paint} BT {_written_numbers(color)} rg {placed} Tm{spaced} {shown} ET Q"
        self._add_content(page, stream.encode())

    def _codes_paint_at(self, page: int, opacity: float) -> str:
        if opacity >= SOLID:
            return ""
        return f" /{self.file.additions.add_opacity(page, opacity)} gs"

    def _resources_of(self, page: int, font_objects: set[int]) -> dict[int, str]:
        """It lacks one when erasing dropped the font, or when it's another page's copy."""
        named: dict[int, str] = {}
        for font in self.fonts(page):
            if not font.in_form:
                named.setdefault(font.font_object, font.resource)
        for font_object in sorted(font_objects - named.keys()):
            resource = self._free_name(page, f"C{font_object}")
            self.file.additions.restore_font(page, resource, font_object)
            self._changed_page(page).keep_named(resource, font_object)
            named[font_object] = resource
        return named

    def _free_name(self, page: int, name: str) -> str:
        taken = {font.resource for font in self._listed_fonts(page)}
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
        """Written to the byte as PyMuPDF's `insert_text` does, but for `letter_spacing`."""
        written = [run for run in runs if run.text]
        if not written:
            return
        pdf_page = self.doc[page]
        # Not `to_pdf_space`, whose numbers differ in the last bit.
        paper_top, corner = pdf_page.mediabox.y1, pdf_page.cropbox_position
        font_objects = self._changed_page(page).named
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
            glyphs = self._glyph_codes(font_objects[run.resource], run.text)
            stream += _written_run(start, glyphs=glyphs, resource=run.resource, style=style)
        self._add_content(page, stream.encode())

    def _paint_at(self, page: int, opacity: float) -> str:
        if opacity >= SOLID:
            return ""
        return f"/{self.file.additions.add_opacity_as_pymupdf(page, opacity)} gs\n"

    def _glyph_codes(self, font_object: int, text: str) -> str:
        """`add_font` adds a font whose codes are its glyphs' numbers."""
        program = self._program_of(font_object)
        return "".join(f"{program.glyph_of(ch):04x}" for ch in text)

    def _program_of(self, font_object: int) -> _MuPDFFont:
        return self.programs.program(
            font_object, lambda: _mupdf_font(self.font_bytes(font_object))
        )

    def keep_pages(self, pages: list[int]) -> None:
        self.doc.select(pages)

    def turn_page(self, page: int, turn_cw: QuarterTurn) -> None:
        self.doc[page].set_rotation(turn_cw)

    def drop_active_content(self) -> bool:
        return self.file.active_content.drop()

    def keeps_xmp_plain(self) -> bool:
        """MuPDF reads and writes that XMP as encrypted all the same."""
        if self.doc.xref_get_key(_TRAILER, "Encrypt/EncryptMetadata") != _FALSE:
            return False
        version = self._protection_version()
        # Below V4, or with no version to read, the flag means nothing: encrypted with the rest.
        if version is None or version < _CRYPT_FILTERS_FROM:
            return False
        return self.doc.xref_xml_metadata() != 0  # 0: it has no XMP

    def _protection_version(self) -> int | None:
        _value_type, version = self.doc.xref_get_key(_TRAILER, "Encrypt/V")
        return int(version) if version.isdigit() else None

    def drop_xmp(self) -> None:
        # The object itself, not the catalog's name for it: a page or PieceInfo may name it too.
        self.doc.update_object(self.doc.xref_xml_metadata(), PDF_NULL)

    def mark_changed_by(self, producer: str, when: datetime) -> None:
        self.file.metadata.mark_changed_by(producer, when)

    def save(self, path: str) -> None:
        self._fill_in_missing_id()
        # Object streams compress the plain objects too: a face's width list is most of it.
        self.doc.save(
            path,
            garbage=constants.GARBAGE_COLLECT,
            deflate=True,
            use_objstms=True,
            encryption=_KEEP_PROTECTION,
        )

    def _fill_in_missing_id(self) -> None:
        protection_type, _protection = self.doc.xref_get_key(_TRAILER, "Encrypt")
        id_type, _id = self.doc.xref_get_key(_TRAILER, "ID")
        protected, has_id = protection_type != PDF_NULL, id_type != PDF_NULL
        if not protected or has_id:
            return
        # Its key was made from the empty first part: a fresh ID written whole would lock it.
        self.doc.xref_set_key(_TRAILER, "ID", _EMPTY_ID)

    def close(self) -> None:
        self.doc.close()

    def replace_font_file(self, font_object: int, font_file: bytes) -> None:
        owner = self._describing_font(font_object)
        # Its inner font in place, or nowhere: MuPDF never adds one so, so it's someone else's.
        if owner is None:
            raise DriverError(
                self._why_kept_whole(font_object),
                debug=f"font {font_object} has no inner font of its own",
            )
        stored = (self.doc.xref_get_key(owner, f"FontDescriptor/{key}") for key in _FONT_FILES)
        value = next(
            (value for value_type, value in stored if value_type == PDF_REFERENCE), None
        )
        if value is None:
            raise DriverError(
                self._why_kept_whole(font_object),
                debug=f"font {font_object} has no file to swap",
            )
        file_xref = object_number(value)
        self.doc.update_stream(file_xref, font_file)
        self.doc.xref_set_key(file_xref, "Length1", str(len(font_file)))

    def _why_kept_whole(self, font_object: int) -> Message:
        _value_type, base_font = self.doc.xref_get_key(font_object, "BaseFont")
        return Message("face_not_trimmed", {"font": base_font.lstrip("/")})

    def has_tags(self) -> bool:
        catalog = self.doc.pdf_catalog()
        value_type, _value = self.doc.xref_get_key(xref=catalog, key=_TAGS_KEY)
        return value_type != PDF_NULL

    def drop_tags(self) -> None:
        catalog = self.doc.pdf_catalog()
        self.doc.xref_set_key(xref=catalog, key=_TAGS_KEY, value=PDF_NULL)

    def _add_content(self, page: int, stream: bytes) -> None:
        self._changed_page(page).wrap(self.doc[page])
        self.file.additions.add_drawing(page, stream)


@dataclass(frozen=True, slots=True)
class _ListedFont:
    font_object: int
    file_type: str
    kind: str
    name: str
    resource: str
    encoding: str
    referencer: int

    @property
    def written_in_place(self) -> bool:
        return self.font_object == _IN_PLACE


def _font_name(read_as: str, by_name: PageTextFonts) -> str:
    if read_as == _UNNAMED_TYPE3.format(font_object=_IN_PLACE):
        return _UNNAMED_TYPE3_IN_PLACE
    return by_name.name_of(read_as)


@dataclass(slots=True)
class _ChangedPage:
    named: dict[str, int] = field(default_factory=dict)
    balanced: bool = False

    def keep_named(self, resource: str, font_object: int) -> None:
        self.named[resource] = font_object

    def wrap(self, pdf_page: pymupdf.Page) -> None:
        # Checked once, as that reads the whole page; nothing the driver does unbalances it.
        if self.balanced:
            return
        if not pdf_page.is_wrapped:
            pdf_page.wrap_contents()
        self.balanced = True


@dataclass(frozen=True, slots=True)
class _Letter:
    text: str
    box: tuple[float, float, float, float]
    origin: tuple[float, float]
    direction: tuple[float, float]
    size: float


# A plain tuple: it sorts at C speed.
type _Middle = tuple[float, float, int]


@dataclass(frozen=True, slots=True, eq=False)
class _PageLetters:
    in_order: list[_Letter] = field(repr=False)
    middles_top_down: list[_Middle] = field(repr=False)

    def inside(self, box: Rect) -> list[_Letter]:
        first = bisect_left(self.middles_top_down, (box.y0, -math.inf, -math.inf))
        past = bisect_right(self.middles_top_down, (box.y1, math.inf, math.inf))
        across = self.middles_top_down[first:past]
        places = sorted(place for _down, x, place in across if box.x0 <= x <= box.x1)
        return [self.in_order[place] for place in places]


def _page_letters(letters: list[_Letter]) -> _PageLetters:
    middles = (_middle_of(letter.box) for letter in letters)
    numbers = [
        (y, x, place)
        for place, (x, y) in enumerate(middles)
        if not (math.isnan(x) or math.isnan(y))
    ]
    return _PageLetters(letters, sorted(numbers))


def _each_line(blocks: list[dict]) -> Iterator[dict]:
    for block in blocks:
        yield from block["lines"]


def _each_letter(lines: Iterable[dict]) -> Iterator[_Letter]:
    for line in lines:
        direction = (line["dir"][0], line["dir"][1])
        for piece in line["spans"]:
            for char in piece["chars"]:
                origin = (char["origin"][0], char["origin"][1])
                yield _Letter(char["c"], tuple(char["bbox"]), origin, direction, piece["size"])


def _placed(letter: _Letter) -> PlacedLetter:
    return PlacedLetter(letter.text, Rect(*letter.box), letter.origin, size=letter.size)


def _text_piece(
    raw: dict, *, direction: tuple[float, float], font: PageFont | None, font_name: str
) -> TextPiece:
    return TextPiece(
        text=raw["text"],
        font=font_name,
        font_object=None if font is None else font.font_object,
        size=raw["size"],
        color=_rgb(raw["color"]),
        opacity=raw["alpha"] / _BYTE_MAX,
        box=Rect(*raw["bbox"]),
        origin=(raw["origin"][0], raw["origin"][1]),
        direction=(direction[0], direction[1]),
    )


def _shows_text(kind: str, value: object) -> bool:
    return kind in _TEXT_FIELD_KINDS and isinstance(value, str) and bool(value.strip())


def _ink_of(drawing: dict[str, Any]) -> float:
    if "s" not in drawing["type"] or not drawing["width"]:
        return 0.0
    return drawing["width"] / 2


def _box_of(piece: tuple[Any, ...], *, ink: float) -> Rect:
    x0, y0, x1, y1 = _outline_of(piece)
    return Rect(x0 - ink, y0 - ink, x1 + ink, y1 + ink)


def _outline_of(piece: tuple[Any, ...]) -> tuple[float, float, float, float]:
    kind, *shape = piece
    if kind == "re":
        return tuple(shape[0])
    if kind == "qu":
        return tuple(shape[0].rect)
    xs, ys = [point.x for point in shape], [point.y for point in shape]
    return min(xs), min(ys), max(xs), max(ys)


def _meets_any(line_box: tuple[float, float, float, float], boxes: list[Rect]) -> bool:
    x0, y0, x1, y1 = line_box
    is_box = x0 <= x1 and y0 <= y1
    if not is_box:
        return True
    return any(_meets(line_box, box) for box in boxes)


def _meets(line_box: tuple[float, float, float, float], box: Rect) -> bool:
    x0, y0, x1, y1 = line_box
    return box.x0 <= x1 and x0 <= box.x1 and box.y0 <= y1 and y0 <= box.y1


def _middle_of(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def _strip_along(letters: list[_Letter]) -> Rect | None:
    points = [point for letter in letters for point in _lifted(letter)]
    if not points:
        return None
    xs, ys = [x for x, _y in points], [y for _x, y in points]
    pad = _STRIP_PAD_PT
    return Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _lifted(letter: _Letter) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = letter.box
    along_x, along_y = letter.direction
    up_x, up_y = along_y, -along_x
    width = abs(x1 - x0) * abs(along_x) + abs(y1 - y0) * abs(along_y)
    height = abs(x1 - x0) * abs(up_x) + abs(y1 - y0) * abs(up_y)
    x, y = letter.origin
    middle_x, middle_y = x + along_x * width / 2, y + along_y * width / 2
    return [
        (middle_x + up_x * height * lift, middle_y + up_y * height * lift)
        for lift in _STRIP_LIFTS
    ]


def _written_name(resource: str) -> str:
    """PyMuPDF hands names back decoded, a byte not UTF-8 as a surrogate."""
    raw = resource.encode("utf-8", "surrogateescape")
    plain = (chr(byte) if _is_plain(byte) else f"#{byte:02X}" for byte in raw)
    return "/" + "".join(plain)


def _is_plain(byte: int) -> bool:
    return byte in _PRINTABLE and byte not in _ENDS_OR_ESCAPES_A_NAME


def _written_numbers(numbers: Iterable[float]) -> str:
    return " ".join(f"{number:.{_PDF_DP}f}" for number in numbers)


def _written_codes(run: CodeRun, *, resource: str, size: float) -> str:
    return f"{_written_name(resource)} {_written_numbers([size])} Tf <{run.codes.hex()}> Tj"


def _written_placing(
    start: tuple[float, float], *, scale_x: float, turn_ccw: QuarterTurn
) -> str:
    cos, sin = QUARTER_TURNS[turn_ccw]
    x, y = start
    return _written_numbers([scale_x * cos, scale_x * sin, -sin, cos, x, y])


def _written_spacing(letter_spacing: float) -> str:
    return f" {letter_spacing:.{_PDF_DP}f} Tc" if letter_spacing else ""


def _written_narrowing(start: tuple[float, float], scale_x: float) -> str:
    """Rounded as PyMuPDF does: the same numbers, to the bit."""
    if scale_x == 1:
        return _NOT_NARROWED
    shift = pymupdf.Matrix(1, 0, 0, 1, *start)
    narrowed = ~shift * pymupdf.Matrix(scale_x, 1) * shift
    return pymupdf.format_g([_matrix_number(number) for number in narrowed]) + " cm\n"


def _matrix_number(number: float) -> float:
    return round(number, _MATRIX_DP) if abs(number) >= _NEAR_ZERO else 0


@dataclass(frozen=True, slots=True)
class _RunStyle:
    paint: str
    scale_x: float
    turn_ccw: QuarterTurn
    setting: str


def _written_setting(
    size: float, color: tuple[float, float, float], letter_spacing: float
) -> str:
    rgb = pymupdf.format_g(color)
    return f"{pymupdf.format_g(size)} Tf {rgb} RG {rgb} rg{_written_spacing(letter_spacing)}"


def _written_run(
    start: tuple[float, float], *, glyphs: str, resource: str, style: _RunStyle
) -> str:
    placed = pymupdf.format_g(_turned_back(start, style.turn_ccw))
    narrowed = _written_narrowing(start, style.scale_x)
    return (
        f"\nq\n{style.paint}BT\n{narrowed}{_written_turn(style.turn_ccw)}"
        f"1 0 0 1 {placed} Tm\n{_written_name(resource)} {style.setting} [<{glyphs}>]TJ"
        "\nET\nQ\n"
    )


def _written_turn(turn_ccw: QuarterTurn) -> str:
    if turn_ccw == 0:
        return ""
    cos, sin = QUARTER_TURNS[turn_ccw]
    return f"{cos} {sin} {-sin} {cos} 0 0 cm\n"


def _turned_back(point: tuple[float, float], turn_ccw: QuarterTurn) -> tuple[float, float]:
    x, y = point
    cos, sin = QUARTER_TURNS[turn_ccw]
    return cos * x + sin * y, cos * y - sin * x


def _drawn(pdf_page: pymupdf.Page, scale: float) -> pymupdf.Pixmap:
    matrix = pdf_page.derotation_matrix * pymupdf.Matrix(scale, scale)
    return pdf_page.get_pixmap(matrix=matrix, alpha=False)


def _cut(drawn: pymupdf.Pixmap, box: pymupdf.IRect) -> bytes:
    part = pymupdf.Pixmap(drawn.colorspace, box & drawn.irect, drawn.alpha)
    part.copy(drawn, part.irect)
    return png_of(part)


def _rgb(packed: int) -> tuple[float, float, float]:
    return (
        ((packed >> 16) & _BYTE_MAX) / _BYTE_MAX,
        ((packed >> 8) & _BYTE_MAX) / _BYTE_MAX,
        (packed & _BYTE_MAX) / _BYTE_MAX,
    )
