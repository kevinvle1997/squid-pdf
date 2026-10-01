"""The one driver there is: PyMuPDF.

Only primitives here (see `core.pdf.driver`): the hard-to-read MuPDF calls come
from `core.pdf.lowlevel`, whose `PdfFile` the driver holds, and the everyday ones are below.
What to make of them is `core.engine`'s. `open_pdf` is the one way in:
nothing outside `core` learns that MuPDF is underneath.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import chain, count

import pymupdf

from squidpdf.core.app.errors import Damaged, Encrypted, TooHeavy
from squidpdf.core.app.message import Message
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT, LIBRARY_VERSION
from squidpdf.core.engine import Engine
from squidpdf.core.fonts import substitute
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.fonts.google import Fetch
from squidpdf.core.pdf.driver import DriverError
from squidpdf.core.pdf.lowlevel import MUPDF_ERRORS, MUPDF_OWN_ERRORS, MUPDF_TOO_HEAVY, PdfFile
from squidpdf.core.plan import letter_widths
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
    Page,
    PageFont,
    Rect,
    TextPiece,
    TextRun,
)

__all__ = [
    "BUILD",
    "open_pdf",
    "result_of",
    "face_widths",
    "MuPDFDriver",
]

_GARBAGE_COLLECT = 2  # drop unused objects; 3 also merges copies, taking pages-squared time

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel
_BYTE_MAX = 255  # the top of one color channel in 0xRRGGBB

# The object the first entry of an array points at: "[15 0 R]" -> 15.
_FIRST_REFERENCE = re.compile(r"\[\s*(\d+)\s+\d+\s+R")
# Where a font description keeps the font file, by the kind of file it is.
_FONT_FILES = ("FontFile2", "FontFile3")

# Read text without images: decoding them took most of the time.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

# A font's kind, as the file names it, in our words. A multiple-master font is a Type 1.
_KINDS: dict[str, FontKind] = {
    "TrueType": "truetype",
    "Type0": "type0",
    "Type1": "type1",
    "MMType1": "type1",
    "Type3": "type3",
}
# How a font's program is stored, as PyMuPDF names it, in our words. "cid" is a CFF
# whose shapes are numbered, not named.
_FILE_TYPES: dict[str, FontFileType] = {
    "ttf": "truetype",
    "otf": "opentype",
    "cff": "cff",
    "cid": "cff",
    "pfa": "type1",
}

_STRIP_PAD_PT = 0.1  # how far an erased strip reaches past the points it runs through
# Where an erased strip runs: this share of each letter's height up from its baseline.
# Every font's glyph boxes reach just above the baseline; few reach the next line's.
_STRIP_LIFTS = (0.05, 0.2)

# The catalog entry for the file's tags: the reading order a screen reader follows.
_TAGS_KEY = "StructTreeRoot"
_PDF_NULL = "null"  # what an absent entry reads as; setting an entry to it removes it

# What drew and judged a page; a new one means earlier images and fidelity may differ.
# Google's copies are part of it: a new pin lends other letters.
_GOOGLE = GOOGLE_FONTS_COMMIT[:7]
BUILD = f"mupdf-{pymupdf.mupdf_version}.fonts-{LIBRARY_VERSION}.google-{_GOOGLE}"


def open_pdf(path: str, *, fetch: Fetch | None = None) -> Engine:
    """The PDF at `path`, open for editing. Use it in a `with`, or close it.

    `fetch` gets Google's copy of a font the file's copies can't draw all of.
    """
    return Engine(MuPDFDriver(path), fetch=fetch)


def result_of[T](task: Callable[[], T]) -> T:
    """What `task()` returns, with MuPDF's own failures raised as the Problems they mean.

    A worker runs its task through this: MuPDF's exceptions hold a pointer, so
    they can't be sent back from another process, and they mean something a
    person can be told. The ErrorController's MuPDF rows say what; anything
    else goes up as it is.
    """
    # Imported here: the imports above are the driver's to change, and wave 2 lifts these.
    from squidpdf.core.app.errors import ErrorController, machine_failure, problem_maker
    from squidpdf.core.pdf.lowlevel import MUPDF_SYSTEM_ERRORS

    mupdf_errors = ErrorController(
        (
            (MUPDF_TOO_HEAVY, problem_maker(TooHeavy)),  # past a limit of MuPDF's own
            (MUPDF_SYSTEM_ERRORS, machine_failure),  # out of memory, or a file it can't open
            (MUPDF_OWN_ERRORS, problem_maker(Damaged)),  # anything else it couldn't make out
        )
    )
    return mupdf_errors.result_of(task)


@cache
def face_widths(face: Face) -> dict[str, float]:
    """Each letter a face we ship draws, within GLYPH_LIST_RANGES, to its width per 1000 em.

    For the font list, where there's no document to open: the same widths the
    engine gives a span drawn in the face.
    """
    return letter_widths(open_face(face), substitute.face_letters(face))


class MuPDFFont:
    """A font file MuPDF has opened. Implements `core.pdf.driver.FontProgram`."""

    def __init__(self, font: pymupdf.Font) -> None:
        """Wrap a font MuPDF has opened."""
        self._font = font
        # A fit check measures the same few letters thousands of times: read each once.
        self._width_in_ems = cache(font.glyph_advance)

    def listed_letters(self) -> list[int]:
        """Every code point MuPDF says the font maps; a trimmed font lists more."""
        return list(self._font.valid_codepoints())

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        return self._width_in_ems(ord(ch))

    def maps(self, ch: str) -> bool:
        """Whether the font has a glyph of its own for `ch`, even an empty one."""
        return self._font.has_glyph(ord(ch)) != 0  # 0 is .notdef: MuPDF found none

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points: what MuPDF's `text_length` says, quicker."""
        return sum(map(self._width_in_ems, map(ord, text))) * size


@cache
def open_face(face: Face) -> MuPDFFont:
    """A face we ship, opened once per process: it measures what `add_font` draws."""
    return MuPDFFont(pymupdf.Font(fontbuffer=face_bytes(face)))


class MuPDFDriver:
    """A PDF open in MuPDF. Implements `core.pdf.driver.PdfDriver`.

    Holds a `core.pdf.lowlevel.PdfFile` for the calls MuPDF's low-level API makes.
    """

    def __init__(self, path: str) -> None:
        """Open the PDF at `path`, only ever as a PDF."""
        try:
            # Left to sniff, MuPDF opens a PNG as a document.
            doc = pymupdf.open(path, filetype="pdf")
        except (
            pymupdf.FileNotFoundError
        ) as exc:  # gone: Python's own error, which callers catch
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
        self._doc = doc
        self._file = PdfFile(doc)
        # The fonts add_font put on each page, by resource name, for erase_text to keep.
        # By the page's own object, whose number stays when the pages are renumbered.
        self._added: dict[int, dict[str, int]] = {}

    def page_count(self) -> int:
        """How many pages the document has, without reading any of them."""
        return len(self._doc)

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for.

        Raises Damaged when the file counts pages it doesn't have.
        """
        try:
            pages = [self._doc[pno] for pno in range(len(self._doc))]
        except (*MUPDF_ERRORS, IndexError) as exc:  # its page list says pages it doesn't hold
            raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None
        return [Page(p.cropbox.width, p.cropbox.height, p.rotation) for p in pages]

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box.

        The clip is mapped into the rotated page, where MuPDF clips.
        """
        pdf_page = self._doc[page]
        box = None if clip is None else pymupdf.Rect(clip.x0, clip.y0, clip.x1, clip.y1)
        pix = pdf_page.get_pixmap(
            matrix=pdf_page.derotation_matrix * pymupdf.Matrix(scale, scale),
            clip=None if box is None else box * pdf_page.rotation_matrix,
            alpha=False,
        )
        return pix.tobytes("png")

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

    def font_bytes(self, xref: int) -> bytes:
        """The font file stored in the PDF. Raises DriverError when MuPDF can't read it out."""
        try:
            _name, _ext, _kind, buffer = self._doc.extract_font(xref)
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
        value_type, _value = self._doc.xref_get_key(owner, "FontDescriptor")
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
        """Where the font's description lives: the font itself, or a Type0's inner font."""
        value_type, value = self._doc.xref_get_key(xref, "DescendantFonts")
        # A simple font describes itself.
        if value_type != "array":
            return xref
        inner = _FIRST_REFERENCE.match(value)
        # None when written out in place: rare, and not worth it.
        return int(inner.group(1)) if inner else None

    def _number(self, xref: int, key: str) -> float | None:
        """A number in object `xref` at `key`, or None when it isn't there or isn't a number."""
        value_type, value = self._doc.xref_get_key(xref, key)
        if value_type not in ("int", "real"):
            return None
        return float(value)

    def font_codes(self, xref: int, code_bytes: int) -> list[FontCode] | None:
        """Each code the font has a letter for, lowest first; None without a letter list.

        Read from MuPDF's own record of the font. Raises DriverError when MuPDF
        can't load it.
        """
        return self._file.font_codes(xref, code_bytes)

    def text_font_name(self, xref: int) -> str | None:
        """The name text in font `xref` reads, often the font file's own; None if unreadable.

        Read from MuPDF's own record of the font.
        """
        return self._file.text_font_name(xref)

    def open_font(self, font_file: bytes) -> MuPDFFont:
        """Open a font file to measure with. Raises DriverError when it isn't one."""
        # No bytes at all: MuPDF would open its own Noto Serif in their place.
        if not font_file:
            raise DriverError(Message("font_unreadable"), debug="no bytes")
        try:
            return MuPDFFont(pymupdf.Font(fontbuffer=font_file))
        except MUPDF_ERRORS as exc:  # MuPDF can't read the bytes as a font
            raise DriverError(Message("font_unreadable"), debug=str(exc)) from exc

    def face_font(self, face: Face) -> MuPDFFont:
        """A face we ship, opened to measure with: it measures what `add_font` draws."""
        return open_face(face)

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """Add a font to the page under the resource name `resource`, or one like it if taken.

        Then `resource` numbered past any the page has: MuPDF, given a name the
        page uses, hands back the font already there. Raises DriverError when
        MuPDF won't add it.
        """
        free_name = self._free_name(page, resource)
        pdf_page = self._doc[page]
        try:
            xref = pdf_page.insert_font(fontname=free_name, fontbuffer=font_file)
        except MUPDF_ERRORS as exc:  # the bytes opened as a font, but the page won't take them
            raise DriverError(Message("font_not_added"), debug=str(exc)) from exc
        self._added.setdefault(pdf_page.xref, {})[free_name] = xref
        return FontResource(free_name, xref)

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Delete the letters whose middle is inside each box, for real.

        The same letters `text_in` reads, so what's erased is what's checked.
        MuPDF deletes every letter whose box a redaction touches, and a letter's
        box runs from its font's ascender to its descender: at usual line spacing
        it reaches the lines above and below. So each box is erased as a thin
        strip just above its own letters' baselines, which other lines' boxes
        don't reach. A box that still has letters afterwards (a font whose boxes
        sit oddly) is erased whole, so old text is never left under new. MuPDF
        also deletes any link a redaction touches, and any font no text on the
        page uses any more: the links go back, and so do the fonts `add_font` added.
        """
        links = self._doc[page].get_links()
        letters = self._letters(page)
        # No letter's middle inside: erase the whole box, as nothing else would.
        self._file.redact(page, [strip_through(letters, box) or box for box in boxes])
        left = self.text_in(page, boxes)
        missed = [box for box, text in zip(boxes, left, strict=True) if text.strip()]
        if missed:
            self._file.redact(page, missed)
        self._restore_links(page, links)
        # .get: a page nothing was added to.
        for resource, xref in self._added.get(self._doc[page].xref, {}).items():
            self._file.restore_font(page, resource, xref)

    def _restore_links(self, page: int, links: list[dict]) -> None:
        """Add back any of `links`, as get_links read them, that the page no longer has."""
        pdf_page = self._doc[page]
        kept = {link_key(link) for link in pdf_page.get_links()}
        for link in links:
            if link_key(link) not in kept:
                pdf_page.insert_link(link)

    def drop_links(self, page: int, boxes: list[Rect]) -> None:
        """Delete every link whose area overlaps one of `boxes`."""
        pdf_page = self._doc[page]
        areas = [pymupdf.Rect(box.x0, box.y0, box.x1, box.y1) for box in boxes]
        for link in pdf_page.get_links():
            if any(pymupdf.Rect(link["from"]).intersects(area) for area in areas):
                pdf_page.delete_link(link)

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
        turn: int,
    ) -> None:
        """Write each run's codes in its font, from `origin` on, on top of the page.

        One text object: each run switches to its font, and the pen moves on by
        that font's widths. PyMuPDF's writers only take letters, so it's written
        out here.
        """
        x, y = self._file.to_pdf_space(page, origin)
        resources = self._resources_of(page, {run.xref for run in runs})
        shown = " ".join(
            f"/{resources[run.xref]} {size:.{_PDF_DP}f} Tf <{run.codes.hex()}> Tj"
            for run in runs
        )
        # See-through: a graphics state that paints at `opacity`.
        paint = f" /{self._file.add_opacity(page, opacity)} gs" if opacity < SOLID else ""
        # Where the text goes: narrowed along its line, turned, and placed.
        cos, sin = QUARTER_TURNS[turn]
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
            self._file.restore_font(page, resource, xref)
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
        turn: int,
    ) -> None:
        """Write each run from its origin, in its font, on top of the page, in order.

        `scale_x` narrows each run from its own start; an `opacity` of 1 is solid.
        `turn` turns each run counter-clockwise about its origin: 0, 90, 180 or 270.
        """
        shape = self._doc[page].new_shape()
        for run in runs:
            at = pymupdf.Point(*run.origin)
            shape.insert_text(
                at,
                run.text,
                fontname=run.resource,
                fontsize=size,
                color=color,
                fill_opacity=opacity,
                rotate=turn,
                morph=(at, pymupdf.Matrix(scale_x, 1)),
            )
        shape.commit(overlay=True)

    def keep_pages(self, pages: list[int]) -> None:
        """Keep only `pages`, in that order; links, bookmarks and fields on the rest go too."""
        self._doc.select(pages)

    def save(self, path: str) -> None:
        """Write the document to `path`, as small as MuPDF makes it."""
        # Object streams compress the plain objects too: a face's width list is most of it.
        self._doc.save(path, garbage=_GARBAGE_COLLECT, deflate=True, use_objstms=True)

    def close(self) -> None:
        """Release the open document."""
        self._doc.close()

    def replace_font_file(self, xref: int, font_file: bytes) -> None:
        """Swap in a new file for font `xref`. It must keep each glyph at its old number."""
        owner = self._describing_font(xref)
        # Its inner font written out in place: MuPDF never adds one so, so it's someone else's.
        if owner is None:
            _value_type, base_font = self._doc.xref_get_key(xref, "BaseFont")
            said = Message("face_not_trimmed", {"font": base_font.lstrip("/")})
            raise DriverError(said, debug=f"font {xref} has its inner font in place")
        # A TrueType face is stored as FontFile2, an OpenType one (Latin Modern) as FontFile3.
        stored = (self._doc.xref_get_key(owner, f"FontDescriptor/{key}") for key in _FONT_FILES)
        value = next(value for value_type, value in stored if value_type == "xref")
        file_xref = int(value.split()[0])  # "7 0 R" -> 7
        self._doc.update_stream(file_xref, font_file)
        # A TrueType file states its size before compression, too.
        self._doc.xref_set_key(file_xref, "Length1", str(len(font_file)))

    def has_tags(self) -> bool:
        """Whether the file is tagged: it has the reading order a screen reader follows."""
        catalog = self._doc.pdf_catalog()  # the file's root entry, where the tags are listed
        value_type, _value = self._doc.xref_get_key(xref=catalog, key=_TAGS_KEY)
        return value_type != _PDF_NULL

    def drop_tags(self) -> None:
        """Remove the file's tags. Saving then drops every page only they pointed at."""
        catalog = self._doc.pdf_catalog()  # the file's root entry, where the tags are listed
        self._doc.xref_set_key(xref=catalog, key=_TAGS_KEY, value=_PDF_NULL)

    def _add_content(self, page: int, stream: bytes) -> None:
        """Draw `stream` on top of everything on the page.

        The page's own drawing is wrapped first, so its settings (color,
        position) can't leak into ours.
        """
        pdf_page = self._doc[page]
        if not pdf_page.is_wrapped:
            pdf_page.wrap_contents()
        xref = self._doc.get_new_xref()
        self._doc.update_object(xref, "<<>>")
        self._doc.update_stream(xref, stream)
        parts = [*pdf_page.get_contents(), xref]
        self._doc.xref_set_key(
            pdf_page.xref, "Contents", "[" + " ".join(f"{p} 0 R" for p in parts) + "]"
        )


@dataclass(frozen=True, slots=True)
class Letter:
    """One letter as get_text("rawdict") reads it, with its line's direction."""

    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1, from ascender to descender
    origin: tuple[float, float]  # where it starts, on its baseline
    direction: tuple[float, float]  # the way its line reads: (1, 0) is left to right


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
