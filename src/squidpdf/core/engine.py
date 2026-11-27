from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from typing import assert_never

import squidpdf.core.constants as constants
from squidpdf.core.app.errors import ErrorController
from squidpdf.core.app.message import Message
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
from squidpdf.core.redaction import RedactionCheck, any_word_left, holds, whole_words, without
from squidpdf.core.text.fidelity import FidelityReport
from squidpdf.core.text.lines import lines_on
from squidpdf.core.text.room import line_rooms_on, rooms_on
from squidpdf.core.text.spans import build_index
from squidpdf.core.types import (
    Face,
    FormField,
    FreeSpace,
    Line,
    Page,
    QuarterTurn,
    Rect,
    RedactedRun,
    Span,
    SpanIndex,
    asked_by_page,
    by_page,
    turn_added,
)
from squidpdf.core.writer import DrawReport, PageWriter, Setting

CORE_ERRORS = ErrorController(MUPDF_FAILURES)

# Google's pin too: a new one may draw and judge a page differently.
_GOOGLE = constants.GOOGLE_FONTS_COMMIT[:7]
BUILD = f"{DRIVER_BUILD}.fonts-{constants.LIBRARY_VERSION}.google-{_GOOGLE}"


@dataclass(frozen=True, slots=True, eq=False)
class LineToDraw:
    span: Span
    text: str
    plan: DrawPlan | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, eq=False)
class Engine:
    driver: PdfDriver
    fonts: DocumentFonts
    plans: DrawPlanner
    writer: PageWriter
    fit: LineFit
    redaction: RedactionCheck

    def index(self) -> SpanIndex:
        """Call it on the pristine file: an edited file's spans get other ids."""
        page_count = self.driver.page_count()
        lines_by_page = [self.driver.text_lines(page) for page in range(page_count)]
        for page, lines in enumerate(lines_by_page):
            self.fonts.keep_text_lines(page, lines)
        return build_index(lines_by_page)

    def page_count(self) -> int:
        return self.driver.page_count()

    def drop_active_content(self) -> bool:
        return self.driver.drop_active_content()

    def drop_plain_xmp(self) -> bool:
        """The library may read and write it as encrypted, so it would come out garbled."""
        if not self.driver.keeps_xmp_plain():
            return False
        self.driver.drop_xmp()
        return True

    def holds_signature(self) -> bool:
        return self.driver.holds_signature()

    def pages(self) -> list[Page]:
        return self.driver.pages()

    def page_image(self, page: int, scale: float) -> bytes:
        return self.driver.page_image(page, scale)

    def box_images(self, page: int, scale: float, boxes: list[Rect]) -> list[bytes]:
        """Each is the page image's own pixels in that box, so a strip over it shows no seam."""
        return self.driver.box_images(page, scale, boxes)

    def in_form_fields(self, spans: Iterable[Span]) -> list[Span]:
        return asked_by_page(spans, self._in_fields_on)

    def _in_fields_on(self, page: int, spans: list[Span]) -> list[Span]:
        fields = self.driver.form_fields(page)
        if not fields:
            return []
        return [span for span in spans if any(_field_draws(field, span) for field in fields)]

    def rooms(self, index: SpanIndex, spans: Iterable[Span]) -> dict[str, float]:
        """Read from the pages as they are, so ask before an erase changes them."""
        wanted = {span.id for span in spans}
        rooms: dict[str, float] = {}
        for page, on_page in by_page(index).items():
            if any(span.id in wanted for span in on_page):
                drawn = self.driver.drawn_boxes(page)
                rooms |= rooms_on(on_page, drawn, wanted=wanted)
        return rooms

    def free_space(self, index: SpanIndex) -> FreeSpace:
        rooms: dict[str, float] = {}
        lines: list[Line] = []
        for page, on_page in by_page(index).items():
            drawn = self.driver.drawn_boxes(page)
            rooms |= rooms_on(on_page, drawn, wanted={span.id for span in on_page})
            lines += self._lines_on(page, on_page, drawn=drawn)
        return FreeSpace(rooms, lines)

    def _lines_on(self, page: int, spans: list[Span], *, drawn: list[Rect]) -> list[Line]:
        in_fields = {span.id for span in self._in_fields_on(page, spans)}
        breaks = [*drawn, *self.driver.link_boxes(page)]
        lines = lines_on(spans, breaks=breaks, in_fields=in_fields)
        rooms = line_rooms_on(spans, drawn, lines=lines)
        return [
            Line(tuple(span.id for span in line), room)
            for line, room in zip(lines, rooms, strict=True)
        ]

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Exact only if the own font redraws it as the page shows it: a redraw is level."""
        return [self._assess_one(span) for span in index]

    def _assess_one(self, span: Span) -> FidelityReport:
        plan = self.plans.plan_for(span, span.text)
        judged = self.fidelity_of(span, plan, own_text=plan)
        return replace(judged, letter_spacing=plan.letter_spacing)

    def fidelity_of(
        self, span: Span, plan: DrawPlan, *, own_text: DrawPlan | None = None
    ) -> FidelityReport:
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, PooledFont):
            return self._own_font_report(span, plan, own_text=own_text)
        if isinstance(drawn_in, Face):
            return self._substitute_report(span, drawn_in)
        assert_never(drawn_in)

    def _own_font_report(
        self, span: Span, plan: DrawPlan, *, own_text: DrawPlan | None
    ) -> FidelityReport:
        own = self.plans.plan_for(span, span.text) if own_text is None else own_text
        unlike = self.plans.unlike(span, plan, own_text=own)
        if unlike is None:
            return FidelityReport(span.id, state="exact", font=span.font, in_file=True)
        return FidelityReport(
            span.id, state="approximate", font=span.font, in_file=True, why=unlike
        )

    def _substitute_report(self, span: Span, drawn_in: Face) -> FidelityReport:
        found = self.fonts.lookup(span)
        why = found.reason if isinstance(found, FontUnusable) else found.why_missing(span.text)
        return FidelityReport(
            span.id,
            state="substitute",
            font=span.font,
            in_file=isinstance(found, PooledFont),
            substitute=drawn_in.name,
            why=why,
            same_widths=self.fonts.same_widths(span, drawn_in),
        )

    def why_not_its_font(self, span: Span, font_file: bytes) -> Message | None:
        return self.fonts.why_not_its_font(span, font_file)

    def widths(self, span: Span) -> dict[str, float]:
        return self.plans.widths(span)

    def font_object_named(
        self, page: int, name: str, *, font_object: int | None = None
    ) -> int | None:
        return self.fonts.font_object_named(page, name, font_object=font_object)

    def shown_name(self, span: Span) -> str:
        return self.fonts.shown_name(span)

    def look_alike(self, span: Span) -> str:
        return self.fonts.look_alike(span).face.name

    def remove(self, spans: list[Span], *, then_drawn: Sequence[LineToDraw]) -> list[Span]:
        """Deleted for real, not covered with a box; returns those the erase couldn't reach."""
        self._read_before_erasing(spans, then_drawn)
        return asked_by_page(spans, self._erased_on)

    def _erased_on(self, page: int, spans: list[Span]) -> list[Span]:
        texts = self.driver.erase_text(
            page, [span.bbox.grown(constants.ROUNDED_PT) for span in spans]
        )
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def _read_before_erasing(self, spans: list[Span], lines: Sequence[LineToDraw]) -> None:
        """Erasing can delete a font no text on the page uses any more, so read first."""
        for span in spans:
            self.fonts.read_before_erasing(span)
        for line in lines:
            if line.plan is None:
                self.plans.plan_for(line.span, line.text)

    def unlink(self, runs: list[RedactedRun], *, lines: Sequence[Span] = ()) -> None:
        """A link can carry the text it's on (a mailto:), or text on its line beside it."""
        for page, on_page in by_page(runs).items():
            self.driver.drop_links(page, [run.bbox for run in on_page])
            holding = partial(holds, whole_words(on_page))
            around = [line.bbox for line in lines if line.page == page]
            self.driver.drop_links(page, around, holding=holding)

    def drop_hidden_copies(self, runs: list[RedactedRun]) -> None:
        """A signature goes whatever it holds: the redaction rewrites the file."""
        # Signatures included: a pattern of no words would match everywhere.
        if not runs:
            return
        for page, on_page in by_page(runs).items():
            words = whole_words(on_page)
            self.driver.rewrite_hidden_copies(page, partial(without, words))
        self.driver.rewrite_document_hidden_copies(partial(without, whole_words(runs)))
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
    ) -> DrawReport:
        planned = self.plans.plan_for(span, text) if plan is None else plan
        drawn_size = span.size if size is None else size
        spacing = planned.letter_spacing_at(drawn_size)
        setting = Setting(
            drawn_size, scale_x=scale_x, turn_ccw=turn_ccw, letter_spacing=spacing
        )
        return self.writer.draw(span, text, plan=planned, setting=setting)

    def turn_pages(self, turns: Mapping[int, QuarterTurn]) -> None:
        if not turns:
            return
        pages = self.driver.pages()
        for page, more in turns.items():
            self.driver.turn_page(page, turn_added(pages[page].turn_cw, more))

    def keep_pages(self, pages: list[int]) -> list[Message]:
        """Call it after the last draw: page numbers change here."""
        every_page_kept = set(pages) == set(range(self.driver.page_count()))
        said = [] if every_page_kept else self._drop_tags()
        self.driver.keep_pages(pages)
        # The planner asks the same `fonts`, so it forgets with them.
        self.fonts.forget_pages()
        self.writer.forget_pages()
        return said

    def _drop_tags(self) -> list[Message]:
        """They point at every page, so they would keep left-out pages in the file."""
        if not self.driver.has_tags():
            return []
        self.driver.drop_tags()
        return [Message("tags_dropped")]

    def mark_changed_by(self, producer: str, when: datetime) -> None:
        self.driver.mark_changed_by(producer, when)

    def save(self, path: str) -> list[Message]:
        """Call it last: afterwards the fonts we added can't draw any new letter."""
        return self.writer.save(path)

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_engine(driver: PdfDriver, *, sources: FontSources) -> Engine:
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


DEFAULT_DRIVER_FACTORY: DriverFactory = open_driver


def open_pdf(
    path: str,
    *,
    sources: FontSources = NO_SOURCES,
    driver_factory: DriverFactory = DEFAULT_DRIVER_FACTORY,
) -> Engine:
    return open_engine(driver_factory(path), sources=sources)


def result_of[T](task: Callable[[], T]) -> T:
    return CORE_ERRORS.result_of(task)


def face_widths(face: Face) -> dict[str, float]:
    """Read anew, not kept: the list reads every face, which would then stay in its worker."""
    font_file = face_bytes_anew(face)
    return letter_widths(open_face(font_file), coverage_of(font_file).previewed_letters())


def _field_draws(field: FormField, span: Span) -> bool:
    words = " ".join(span.text.split())
    shown = " ".join(field.value.split())
    return bool(words) and words in shown and _middle_inside(span.bbox, field.box)


def _middle_inside(box: Rect, area: Rect) -> bool:
    x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    return area.x0 <= x <= area.x1 and area.y0 <= y <= area.y1
