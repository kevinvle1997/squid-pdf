"""A PDF open for editing: its spans, what we can promise about each, and the edits on it.

Written against a `PdfDriver`'s primitives (remove, draw), so it's the same over any PDF
library and never learns what a Replace or a Redact is: that keeps `core` free of feature
imports. Open one with `core.open_pdf`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import assert_never

from squidpdf.core.app.errors import ErrorController
from squidpdf.core.app.message import Message
from squidpdf.core.constants import GOOGLE_FONTS_COMMIT, LIBRARY_VERSION
from squidpdf.core.fit import LineFit
from squidpdf.core.fonts.attached import AttachedFonts
from squidpdf.core.fonts.catalog import face_bytes_anew
from squidpdf.core.fonts.coverage import coverage_of
from squidpdf.core.fonts.document import NO_SOURCES, DocumentFonts, FontSources
from squidpdf.core.fonts.embedded import FontUnusable
from squidpdf.core.fonts.google import GoogleFontController
from squidpdf.core.fonts.pool import PooledFont
from squidpdf.core.pdf.driver import DriverFactory, PdfDriver

# The one import that names the library: its driver is `open_pdf`'s default.
from squidpdf.core.pdf.mupdf import DRIVER_BUILD, MUPDF_FAILURES, open_driver, open_face
from squidpdf.core.plan import DrawPlan, DrawPlanner, letter_widths
from squidpdf.core.redaction import RedactionCheck, any_word_left, whole_words, without
from squidpdf.core.text.fidelity import FidelityReport
from squidpdf.core.text.room import rooms_on
from squidpdf.core.text.spans import build_index
from squidpdf.core.types import (
    Face,
    FormField,
    Page,
    QuarterTurn,
    Rect,
    Span,
    SpanIndex,
    asked_by_page,
    by_page,
    turn_added,
)
from squidpdf.core.writer import PageWriter, Setting

# Core's own: every failure below the API, said as the Problem it means.
CORE_ERRORS = ErrorController(MUPDF_FAILURES)

# What drew and judged a page, Google's pin too: a new one may draw and judge it differently.
_GOOGLE = GOOGLE_FONTS_COMMIT[:7]
BUILD = f"{DRIVER_BUILD}.fonts-{LIBRARY_VERSION}.google-{_GOOGLE}"


@dataclass(frozen=True, slots=True, eq=False)
class LineToDraw:
    """A line `draw` draws after the erase: its span, its text, and a fit's plan, if any."""

    span: Span
    text: str
    plan: DrawPlan | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, eq=False)
class Engine:
    """A PDF open for editing. Use it in a `with`, or close it. Made by `open_engine`."""

    driver: PdfDriver
    fonts: DocumentFonts
    # Which font draws a line and what comes out: one answer the fit and the draw share.
    plans: DrawPlanner
    writer: PageWriter
    # The questions a fit asks, and a redaction's checks: each asked from outside on its own.
    fit: LineFit
    redaction: RedactionCheck

    # What the document says.

    def index(self) -> SpanIndex:
        """Every editable span in the document as it is now.

        Call it on the pristine file: an edited file's spans get other ids.
        """
        page_count = self.driver.page_count()
        lines_by_page = [self.driver.text_lines(page) for page in range(page_count)]
        # A usual gap is read from the same text: kept, so no page's is read twice.
        for page, lines in enumerate(lines_by_page):
            self.fonts.keep_text_lines(page, lines)
        return build_index(lines_by_page)

    def page_count(self) -> int:
        """How many pages the document has, without reading any of them."""
        return self.driver.page_count()

    def drop_active_content(self) -> bool:
        """Delete what acts on its own or reaches outside the file; whether there was any."""
        return self.driver.drop_active_content()

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        return self.driver.pages()

    def page_image(self, page: int, scale: float) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point.

        No alpha channel: the page is white whatever the app's theme (Rule 2).
        Unrotated, so the image lines up with the span boxes; the browser turns it.
        """
        return self.driver.page_image(page, scale)

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]:
        """Each box of the page unrotated as a PNG, `scale` pixels per point.

        Each is the page image's own pixels in that box, so a strip over it shows no seam.
        The page is drawn once for them all.
        """
        return self.driver.box_images(page, scale, boxes)

    def in_form_fields(self, spans: Iterable[Span]) -> list[Span]:
        """The spans a form field draws, not the page, so an edit can't change them yet.

        A field draws a span when the span sits inside it and its words are part
        of the value the field shows: text the page draws there, a hint printed
        in an empty field, say, is the page's, and an edit changes it.
        """
        return asked_by_page(spans, self._in_fields_on)

    def _in_fields_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, that a form field draws."""
        fields = self.driver.form_fields(page)
        # A page with no field showing text: nothing to find, so no span is looked at.
        if not fields:
            return []
        return [span for span in spans if any(_field_draws(field, span) for field in fields)]

    def rooms(self, index: SpanIndex, spans: Iterable[Span]) -> dict[str, float]:
        """The room of each of `spans`, by id: how far it can run (`core.text.room`).

        Read from the pages as they are, so ask before an erase changes them.
        """
        wanted = {span.id for span in spans}
        rooms: dict[str, float] = {}
        for page, on_page in by_page(index).items():
            if any(span.id in wanted for span in on_page):
                drawn = self.driver.drawn_boxes(page)
                rooms |= rooms_on(on_page, drawn, wanted=wanted)
        return rooms

    # What we can promise about it.

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Judge every span in the index as exact, approximate or substitute.

        Exact only if the file's own font redraws it as the page shows it: a redraw is level,
        left to right and closed up, so text that isn't comes back unlike itself.
        """
        return [self._assess_one(span) for span in index]

    def _assess_one(self, span: Span) -> FidelityReport:
        """Exact, approximate or substitute, for one span."""
        plan = self.plans.plan_for(span, span.text)
        return replace(self._judged(span, plan), letter_spacing=plan.letter_spacing)

    def _judged(self, span: Span, plan: DrawPlan) -> FidelityReport:
        """Exact, approximate or substitute, for one span, as `plan` draws its own text."""
        drawn_in = plan.drawn_in
        # The file's own font redraws its own text, as it is or not quite.
        if isinstance(drawn_in, PooledFont):
            unlike = self.plans.unlike(span, plan)
            if unlike is None:
                return FidelityReport(span.id, "exact", span.font, in_file=True)
            return FidelityReport(span.id, "approximate", span.font, in_file=True, why=unlike)
        # A face we ship draws it in the font's place.
        if isinstance(drawn_in, Face):
            return self._substitute_report(span, drawn_in)
        assert_never(drawn_in)

    def _substitute_report(self, span: Span, drawn_in: Face) -> FidelityReport:
        """Substitute: `drawn_in`, a face we ship, draws the span in its font's place."""
        found = self.fonts.lookup(span)
        why = found.reason if isinstance(found, FontUnusable) else found.why_missing(span.text)
        match = self.fonts.look_alike(span)
        return FidelityReport(
            span.id,
            "substitute",
            span.font,
            in_file=isinstance(found, PooledFont),
            substitute=drawn_in.name,
            why=why,
            same_widths=match.same_widths and drawn_in == match.face,
        )

    def why_not_its_font(self, span: Span, font_file: bytes) -> Message | None:
        """Why `font_file` isn't the span's font, as the user's own copy; None when it is."""
        return self.fonts.why_not_its_font(span, font_file)

    def widths(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em."""
        return self.plans.widths(span)

    def shown_name(self, span: Span) -> str:
        """The name the reader knows the span's font by: a Type3 by its copies' name."""
        return self.fonts.shown_name(span)

    def look_alike(self, span: Span) -> str:
        """The face we ship that draws an edit with a letter no copy of the span's font has."""
        return self.fonts.look_alike(span).face.name

    # Changing it.

    def remove(self, spans: list[Span], *, then_drawn: Sequence[LineToDraw]) -> list[Span]:
        """Delete these spans' text for real, not by covering it with a box.

        Returns the spans with a word still in their box, which the erase couldn't reach, as a
        form field's value. `then_drawn` is read first, while the page still has it. One box per
        span, which covers its fragments: cost grows per box. A FreeText goes; lines don't.
        """
        self._read_before_erasing(spans, then_drawn)
        return asked_by_page(spans, self._erased_on)

    def _erased_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Erase `spans`, all on `page`; returns those with any word of their text left."""
        texts = self.driver.erase_text(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def _read_before_erasing(self, spans: list[Span], lines: Sequence[LineToDraw]) -> None:
        """Read what `draw` needs while the page still has it.

        Erasing can delete a font no text on the page uses any more, so each span's
        fonts and gaps are read first, and each line not planned yet is planned.
        """
        for span in spans:
            self.fonts.read_before_erasing(span)
        for line in lines:
            # A fit's plan already took in its fonts.
            if line.plan is None:
                self.plans.plan_for(line.span, line.text)

    def unlink(self, spans: list[Span]) -> None:
        """Delete every link over these spans: a link can carry the text it's on (a mailto:)."""
        for page, on_page in by_page(spans).items():
            self.driver.drop_links(page, [span.bbox for span in on_page])

    def drop_hidden_copies(self, spans: list[Span]) -> None:
        """Delete these spans' words from every hidden copy, as whole words, and all signatures.

        A signature goes whatever it holds: the redaction rewrites the file, so none would hold.
        """
        # No span: nothing to delete, and a pattern of no words would match everywhere.
        if not spans:
            return
        for page, on_page in by_page(spans).items():
            words = whole_words(on_page)
            self.driver.rewrite_hidden_copies(page, partial(without, words))
        self.driver.rewrite_document_hidden_copies(partial(without, whole_words(spans)))
        self.driver.drop_signatures()

    def draw(
        self,
        span: Span,
        text: str,
        *,
        plan: DrawPlan | None = None,
        size: float | None = None,
        scale_x: float = 1.0,
        turn_ccw: QuarterTurn = 0,
    ) -> list[Message]:
        """Redraw `text` at the span's baseline, in the font `LineFit.plan_for` names.

        `plan` is `LineFit.plan_for`'s answer from before the erase; None plans it here.
        `scale_x` narrows the run from its start; `turn_ccw` turns it on the page
        unrotated. Returns what came out other than asked.
        """
        planned = self.plans.plan_for(span, text) if plan is None else plan
        drawn_size = span.size if size is None else size
        spacing = planned.letter_spacing_at(drawn_size)
        setting = Setting(drawn_size, scale_x, turn_ccw, letter_spacing=spacing)
        return self.writer.draw(span, text, plan=planned, setting=setting)

    def turn_pages(self, turns: Mapping[int, QuarterTurn]) -> None:
        """Turn each page in `turns` that much further clockwise, on top of its own turn.

        Nothing on the page moves: the turn is how a reader's viewer shows it.
        """
        # No page turned, as in most exports: no page need be read for its own turn.
        if not turns:
            return
        pages = self.driver.pages()
        for page, more in turns.items():
            self.driver.turn_page(page, turn_added(pages[page].turn_cw, more))

    def keep_pages(self, pages: list[int]) -> list[Message]:
        """Keep only `pages`, in that order: page `pages[0]` becomes the first.

        Call it after the last draw: page numbers change here. Returns what
        came out other than asked.
        """
        every_page_kept = set(pages) == set(range(self.driver.page_count()))
        said = [] if every_page_kept else self._drop_tags()
        self.driver.keep_pages(pages)
        # Looked up and named by page number, and those just changed. The planner
        # asks the same `fonts`, so it forgets with them.
        self.fonts.forget_pages()
        self.writer.forget_pages()
        return said

    def _drop_tags(self) -> list[Message]:
        """Drop the file's tags, and say so if it had any.

        They point at every page, so they would keep left-out pages in the file.
        """
        if not self.driver.has_tags():
            return []
        self.driver.drop_tags()
        return [Message("tags_dropped")]

    def save(self, path: str) -> list[Message]:
        """Write the document to `path`, the fonts we added cut to the letters drawn in them.

        Call it last: afterwards they can't draw any new letter. Returns
        anything that came out other than asked, for the edge to put into words.
        """
        return self.writer.save(path)

    def close(self) -> None:
        """Release the open document."""
        self.driver.close()

    def __enter__(self) -> Engine:
        """Lets the engine be used as `with open_pdf(path) as engine:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the document when the `with` block ends."""
        self.close()


def open_engine(driver: PdfDriver, *, sources: FontSources) -> Engine:
    """Take over an open document, with nothing looked up or added yet.

    `sources` are where a font may borrow the letters its copies in the file lack.
    """
    fetch = sources.google
    google = None if fetch is None else GoogleFontController(driver, fetch)
    attached = AttachedFonts(driver, sources.attached)
    fonts = DocumentFonts(driver, attached=attached, google=google)
    plans = DrawPlanner(fonts, driver)
    return Engine(
        driver,
        fonts=fonts,
        plans=plans,
        writer=PageWriter(driver, plans),
        fit=LineFit(fonts, plans),
        redaction=RedactionCheck(driver),
    )


# What opens a PDF unless a caller injects another: the one library there is.
DEFAULT_DRIVER_FACTORY: DriverFactory = open_driver


def open_pdf(
    path: str,
    *,
    sources: FontSources = NO_SOURCES,
    driver_factory: DriverFactory = DEFAULT_DRIVER_FACTORY,
) -> Engine:
    """The PDF at `path`, open in `driver_factory`'s driver. Use it in a `with`, or close it."""
    return open_engine(driver_factory(path), sources=sources)


def result_of[T](task: Callable[[], T]) -> T:
    """What `task()` returns, with the library's failures raised as the Problems they mean."""
    return CORE_ERRORS.result_of(task)


def face_widths(face: Face) -> dict[str, float]:
    """Each letter a face we ship draws, within GLYPH_LIST_RANGES, to its width per 1000 em.

    For the font list, where no document is open: the same widths a span in the face gets.
    Read anew, not kept: the list reads every face, which would then stay in its worker.
    """
    font_file = face_bytes_anew(face)
    return letter_widths(open_face(font_file), coverage_of(font_file).previewed_letters())


def _field_draws(field: FormField, span: Span) -> bool:
    """Whether `field` draws `span`: it sits inside, and its words are part of the value."""
    words = " ".join(span.text.split())
    shown = " ".join(field.value.split())
    return bool(words) and words in shown and _middle_inside(span.bbox, field.box)


def _middle_inside(box: Rect, area: Rect) -> bool:
    """Whether the middle of `box` lies inside `area`."""
    x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    return area.x0 <= x <= area.x1 and area.y0 <= y <= area.y1
