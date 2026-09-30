"""The one driver there is: PyMuPDF.

Only primitives here (see `core.driver`): the hard-to-read MuPDF calls come
from `core.pdf`, which the driver extends, and the everyday ones are below.
What to make of them is `core.engine`'s. `open_pdf` is the one way in:
nothing outside `core` learns that MuPDF is underneath.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from functools import cache
from itertools import chain, count

import pymupdf

from squidpdf.core import faces
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT, LIBRARY_VERSION
from squidpdf.core.driver import DriverError
from squidpdf.core.engine import Engine, letter_widths
from squidpdf.core.errors import Damaged, Encrypted, TooHeavy
from squidpdf.core.fonts import face_bytes
from squidpdf.core.google import Fetch
from squidpdf.core.message import Message
from squidpdf.core.pdf import MUPDF_ERRORS, MUPDF_OWN_ERRORS, MUPDF_TOO_HEAVY, PdfFile
from squidpdf.core.types import (
    QUARTER_TURNS,
    SOLID,
    CodeRun,
    Face,
    FontResource,
    Page,
    Rect,
    TextRun,
)

__all__ = [
    "BUILD",
    "open_pdf",
    "result_of",
    "face_widths",
    "MuPDFDriver",
    "write_sample",
    "write_dense",
]

_GARBAGE_COLLECT_MAX = 3  # PyMuPDF's highest level: dedupe + drop unused objects

# The long fixture's page: a contract's body text, set the way a word processor sets it.
_DENSE_SIZE = 10.5  # points
_DENSE_LEADING = 14.0  # points from one baseline to the next
_DENSE_TOP = 108.0  # the first body line's baseline
_DENSE_BOTTOM = 770.0  # no baseline below this
_DENSE_WORDS = (
    "the provider shall deliver services under this agreement within the term and "
    "notify the client in writing of any change to the schedule fees or invoices "
    "each party keeps confidential information secret and uses it only for the "
    "purpose agreed liability is limited to the fees paid in the period before the claim"
).split()
_DENSE_TERMS = ("the Services", "the Client", "the Provider", "Confidential Information")

_PDF_DP = 4  # decimals written into a content stream, far below a device pixel

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
    person can be told. Past a limit or out of memory is too heavy; anything
    else MuPDF couldn't do with the file, damaged.
    """
    try:
        return task()
    except MUPDF_TOO_HEAVY as exc:  # MuPDF ran out of memory, or past a limit of its own
        raise TooHeavy(debug=f"{type(exc).__name__}: {exc}") from None
    except MUPDF_OWN_ERRORS as exc:  # MuPDF couldn't make sense of the file
        raise Damaged(debug=f"{type(exc).__name__}: {exc}") from None


@cache
def face_widths(face: Face) -> dict[str, float]:
    """Each letter a face we ship draws, within GLYPH_LIST_RANGES, to its width per 1000 em.

    For the font list, where there's no document to open: the same widths the
    engine gives a span drawn in the face.
    """
    return letter_widths(open_face(face), faces.face_letters(face))


class MuPDFFont:
    """A font file MuPDF has opened. Implements `core.driver.FontProgram`."""

    def __init__(self, font: pymupdf.Font) -> None:
        """Wrap a font MuPDF has opened."""
        self._font = font

    def listed_letters(self) -> list[int]:
        """Every code point MuPDF says the font maps; a trimmed font lists more."""
        return list(self._font.valid_codepoints())

    def advance(self, ch: str) -> float:
        """How far `ch` moves the pen, in ems."""
        return self._font.glyph_advance(ord(ch))

    def maps(self, ch: str) -> bool:
        """Whether the font has a glyph of its own for `ch`, even an empty one."""
        return self._font.has_glyph(ord(ch)) != 0  # 0 is .notdef: MuPDF found none

    def width(self, text: str, size: float) -> float:
        """How wide `text` is at `size` points."""
        return self._font.text_length(text, fontsize=size)


@cache
def open_face(face: Face) -> MuPDFFont:
    """A face we ship, opened once per process: it measures what `add_font` draws."""
    return MuPDFFont(pymupdf.Font(fontbuffer=face_bytes(face)))


class MuPDFDriver(PdfFile):
    """A PDF open in MuPDF. Implements `core.driver.PdfDriver`."""

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
        super().__init__(doc)
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
        pg = self._doc[page]
        box = None if clip is None else pymupdf.Rect(clip.x0, clip.y0, clip.x1, clip.y1)
        pix = pg.get_pixmap(
            matrix=pg.derotation_matrix * pymupdf.Matrix(scale, scale),
            clip=None if box is None else box * pg.rotation_matrix,
            alpha=False,
        )
        return pix.tobytes("png")

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

    def add_font(self, page: int, font_file: bytes, *, name: str) -> FontResource:
        """Add a font to the page, as `name` unless the page already has a font by it.

        Then as `name` numbered past any the page has: MuPDF, given a name the
        page uses, hands back the font already there. Raises DriverError when
        MuPDF won't add it.
        """
        resource = self._free_name(page, name)
        pg = self._doc[page]
        try:
            xref = pg.insert_font(fontname=resource, fontbuffer=font_file)
        except MUPDF_ERRORS as exc:  # the bytes opened as a font, but the page won't take them
            raise DriverError(Message("font_not_added"), debug=str(exc)) from exc
        self._added.setdefault(pg.xref, {})[resource] = xref
        return FontResource(resource, xref)

    def erase_text(self, page: int, boxes: list[Rect]) -> None:
        """Delete the letters whose middle is inside these boxes, for real.

        Images, drawings, links and the fonts `add_font` put on the page stay.
        """
        super().erase_text(page, boxes)
        # MuPDF drops a font no text on the page uses any more: put back ours.
        # .get: a page nothing was added to.
        for resource, xref in self._added.get(self._doc[page].xref, {}).items():
            self.restore_font(page, resource, xref)

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
        x, y = self.to_pdf_space(page, origin)
        shown = " ".join(
            f"/{self._resource_of(page, run.font)} {size:.{_PDF_DP}f} Tf <{run.codes.hex()}> Tj"
            for run in runs
        )
        # See-through: a graphics state that paints at `opacity`.
        paint = f" /{self.add_opacity(page, opacity)} gs" if opacity < SOLID else ""
        # Where the text goes: narrowed along its line, turned, and placed.
        cos, sin = QUARTER_TURNS[turn]
        matrix = [scale_x * cos, scale_x * sin, -sin, cos, x, y]
        placed = " ".join(f"{number:.{_PDF_DP}f}" for number in matrix)
        rgb = " ".join(f"{channel:.{_PDF_DP}f}" for channel in color)
        # Save the page's settings, set color, place the text, write each run in
        # its font and size, then put the settings back.
        stream = f"q{paint} BT {rgb} rg {placed} Tm {shown} ET Q"
        self.add_content(page, stream.encode())

    def _resource_of(self, page: int, xref: int) -> str:
        """The page's resource name for font `xref`, giving it one when the page has none.

        It has none when erasing dropped the font, or when it's another page's copy.
        """
        on_page = (font for font in self.fonts(page) if not font.in_form)
        listed = (font.resource for font in on_page if font.xref == xref)
        # The first name the page lists it under, in MuPDF's order.
        resource = next(listed, None)
        if resource is not None:
            return resource
        resource = self._free_name(page, f"C{xref}")
        self.restore_font(page, resource, xref)
        return resource

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
                fontname=run.font,
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
        self._doc.save(path, garbage=_GARBAGE_COLLECT_MAX, deflate=True, use_objstms=True)

    def close(self) -> None:
        """Release the open document."""
        self._doc.close()


def write_sample(path: str) -> None:
    """A two-page sample: one page whose fonts are only named, one where one is stored."""
    doc = pymupdf.open()
    # "tibo" and "tiro" are MuPDF's short names for Times Bold and Times Roman,
    # which it never puts in the file.
    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        "This agreement is made on 14 March 2026 between",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 146),
        "Wescott Analytics Ltd and Lindqvist & Rowe LLP.",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 176),
        "The Client shall pay 48,500 per quarter in arrears.",
        fontname="tiro",
        fontsize=11,
    )

    # Embedded then subsetted, the way a real generator leaves it, so only the
    # glyphs this page used survive and typing an accent will fail.
    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text((72, 96), "Schedule 1 - Scope of work", fontname="emb", fontsize=12)
    embedded.insert_text(
        (72, 124),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()


def write_dense(path: str, *, pages: int) -> None:
    """A long contract of made-up clauses, for timing the browser on full pages.

    Every line is a span, and one in three has a defined term set in bold, as a
    contract's are, so a page carries as many spans as a real one. The words are
    the same on every run. Page 1 opens with the sample's line, for a test to find.
    """
    choose = random.Random(0)
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((72, 84), f"Schedule {number + 1}", fontname="tibo", fontsize=13)
        baseline = _DENSE_TOP
        if number == 0:
            opening = "This agreement is made on 14 March 2026 between"
            page.insert_text((72, baseline), opening, fontname="tiro", fontsize=_DENSE_SIZE)
            baseline += _DENSE_LEADING
        while baseline <= _DENSE_BOTTOM:
            line = [choose.choice(_DENSE_WORDS) for _ in range(choose.randint(9, 12))]
            runs = [(" ".join(line) + " ", "tiro")]
            if choose.random() < 1 / 3:
                cut = choose.randint(2, len(line) - 2)
                runs = [
                    (" ".join(line[:cut]) + " ", "tiro"),
                    (choose.choice(_DENSE_TERMS) + " ", "tibo"),
                    (" ".join(line[cut:]), "tiro"),
                ]
            x = 72.0
            for text, font in runs:
                page.insert_text((x, baseline), text, fontname=font, fontsize=_DENSE_SIZE)
                x += pymupdf.get_text_length(text, fontname=font, fontsize=_DENSE_SIZE)
            baseline += _DENSE_LEADING
    doc.save(path)
    doc.close()
