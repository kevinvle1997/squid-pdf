"""A PDF open for editing: its spans, what we can promise about each, and the edits on it.

The product's own logic, written against a `core.pdf.driver.PdfDriver`'s primitives,
so it's the same over any PDF library. Open one with `core.open_pdf`.

The engine speaks only in primitives (remove, draw), so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
What draws a line is `core.plan`'s, and drawing it is `core.writer`'s: the
engine asks both.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import assert_never

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.document import DocumentFonts, FontSources
from squidpdf.core.fonts.google import GoogleFontController
from squidpdf.core.fonts.pool import PooledFont
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.plan import DrawPlan, DrawPlanner
from squidpdf.core.text.fidelity import FidelityReport
from squidpdf.core.text.spacing import lacks_space
from squidpdf.core.text.spans import build_index
from squidpdf.core.types import Face, FormField, Page, QuarterTurn, Rect, Span, SpanIndex
from squidpdf.core.writer import PageWriter, Setting

__all__ = [
    "Engine",
    "LineToDraw",
]


@dataclass(frozen=True, slots=True)
class LineToDraw:
    """A line `draw` will draw once the erasing is done: the span it's at, and its text."""

    span: Span
    text: str


class Engine:
    """A PDF open for editing. Use it in a `with`, or close it."""

    def __init__(self, driver: PdfDriver, *, sources: FontSources) -> None:
        """Take over an open document, with nothing looked up or added yet.

        `sources` are where a font may borrow the letters its copies in the file lack.
        """
        self._driver = driver
        fetch = sources.google
        self._google = None if fetch is None else GoogleFontController(driver, fetch=fetch)
        self._fonts = DocumentFonts(driver, google=self._google)
        # Which font draws a line and what comes out: one answer the fit and the draw share.
        self._plans = DrawPlanner(self._fonts, driver)
        self._writer = PageWriter(driver)

    # What the document says.

    def index(self) -> SpanIndex:
        """Every editable span, extracted once from the pristine document."""
        page_count = self._driver.page_count()
        return build_index(self._driver.text_lines(page) for page in range(page_count))

    def page_count(self) -> int:
        """How many pages the document has, without reading any of them."""
        return self._driver.page_count()

    def pages(self) -> list[Page]:
        """Each page's size, unrotated like the span boxes, and the turn it asks for."""
        return self._driver.pages()

    def page_image(self, page: int, scale: float, clip: Rect | None = None) -> bytes:
        """The page unrotated as a PNG, `scale` pixels per point, or only the `clip` box.

        No alpha channel: the page is white whatever the app's theme (Rule 2).
        Unrotated, so the image lines up with the span boxes; the browser turns it.
        """
        return self._driver.page_image(page, scale, clip)

    def in_form_fields(self, spans: Iterable[Span]) -> list[Span]:
        """The spans a form field draws, not the page, so an edit can't change them yet.

        A field draws a span when the span sits inside it and its words are part
        of the value the field shows: text the page draws there, a hint printed
        in an empty field, say, is the page's, and an edit changes it.
        """
        return [
            span
            for page, on_page in by_page(spans).items()
            for span in self._in_fields_on(page, on_page)
        ]

    def _in_fields_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, that a form field draws."""
        fields = self._driver.form_fields(page)
        # A page with no field showing text: nothing to find, so no span is looked at.
        if not fields:
            return []
        return [span for span in spans if any(field_draws(field, span) for field in fields)]

    # What we can promise about it.

    def assess(self, index: SpanIndex) -> list[FidelityReport]:
        """Judge every span in the index as exact, approximate or substitute.

        Exact only if the file's copies of the span's font redraw its own text
        as the page shows it now: `draw` swaps the run otherwise, so a redraw of
        it would be in the substitute, and it draws level and closed up, so a
        line turned or spaced out would come back unlike itself.
        """
        return [self._assess_one(span) for span in index]

    def _assess_one(self, span: Span) -> FidelityReport:
        """Exact, approximate or substitute, for one span."""
        plan = self._plans.plan_for(span, span.text)
        drawn_in = plan.drawn_in
        # The file's own font redraws its own text, as it is or not quite.
        if isinstance(drawn_in, PooledFont):
            unlike = self._plans.unlike(span, plan)
            if unlike is None:
                return FidelityReport(span.id, "exact", span.font, in_file=True)
            return FidelityReport(span.id, "approximate", span.font, in_file=True, why=unlike)
        # A face we ship draws it in the font's place.
        if isinstance(drawn_in, Face):
            return self._substitute_report(span, drawn_in)
        assert_never(drawn_in)

    def _substitute_report(self, span: Span, drawn_in: Face) -> FidelityReport:
        """Substitute: `drawn_in`, a face we ship, draws the span in its font's place."""
        own = self._fonts.own(span)
        match = self._fonts.look_alike(span)
        return FidelityReport(
            span.id,
            "substitute",
            span.font,
            in_file=own is not None,
            substitute=drawn_in.name,
            why=self._fonts.why_not(span) if own is None else own.why_missing(span.text),
            same_widths=match.same_widths and drawn_in == match.face,
        )

    def widths(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em."""
        return self._plans.widths(span)

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        """How `text` is drawn at this span: worked out once, for a fit to ask all it needs of.

        Its `missing` are the letters no copy of the span's font in the file really
        draws: each is checked for a shape, since a trimmed (subset) font still lists
        letters whose shapes were emptied; a font not in the file is checked against
        its look-alike. Its `left_out` are those no font we have draws. `width_of`
        and `substitute` read it too.
        """
        return self._plans.plan_for(span, text)

    def width_of(self, span: Span, plan: DrawPlan) -> float:
        """How wide `plan`'s line renders, placed as `draw` places it, at this span's size."""
        return self._plans.width_of(span, plan, size=span.size)

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, placed as `draw` places it, at this span's size."""
        return self.width_of(span, self.plan_for(span, text))

    def substitute(self, span: Span, text: str, *, plan: DrawPlan) -> str:
        """The face we ship that draws `text` when the span's own font can't: "Carlito Bold".

        `plan` is `text`'s, from `plan_for`.
        """
        drawn_in = plan.drawn_in
        # The own font draws it: the face that would if the page wouldn't take the font.
        if isinstance(drawn_in, PooledFont):
            return self._plans.substitute_for(span, text).face.name
        # A face we ship draws it.
        if isinstance(drawn_in, Face):
            return drawn_in.name
        assert_never(drawn_in)

    # Changing it.

    def remove(self, spans: list[Span], *, then_drawn: Sequence[LineToDraw]) -> list[Span]:
        """Delete these spans' text for real, not by covering it with a box.

        Returns the spans with any word of their text still in their box, as
        `still_there` says it: the erase couldn't reach it, as a form field draws
        its value, not the page. `then_drawn` are the lines `draw` will draw
        after: what they need is read first, while the page still has it. One
        box per span, not per fragment: the cost grows with the box count, and a
        span's box covers its fragments. Lines, underlines and links stay.
        """
        self._read_before_erasing(spans, then_drawn)
        left = (self._erased_on(page, on_page) for page, on_page in by_page(spans).items())
        return [span for on_page in left for span in on_page]

    def _erased_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Erase `spans`, all on `page`; returns those with any word of their text left."""
        texts = self._driver.erase_text(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def _read_before_erasing(self, spans: list[Span], lines: Sequence[LineToDraw]) -> None:
        """Read what `draw` needs while the page still has it.

        Erasing can delete a font no text on the page uses any more. So each
        span's font and look-alike are read first, and the page's gaps a new
        space is measured against; and each line to draw is planned, which takes
        in every copy of a font it borrows a letter from.
        """
        for span in spans:
            own = self._fonts.own(span)
            self._fonts.look_alike(span)
            if own is not None and lacks_space(own):
                self._fonts.usual_gap(span, own)
        for line in lines:
            self._plans.plan_for(line.span, line.text)

    def unlink(self, spans: list[Span]) -> None:
        """Delete every link over these spans: a link can carry the text it's on (a mailto:)."""
        for page, on_page in by_page(spans).items():
            self._driver.drop_links(page, [span.bbox for span in on_page])

    def draw(
        self,
        span: Span,
        text: str,
        *,
        size: float | None = None,
        scale_x: float = 1.0,
        turn_ccw: QuarterTurn = 0,
    ) -> list[Message]:
        """Redraw `text` at the span's baseline, in the font `plan_for` names.

        `size` in points replaces the span's own; `scale_x` narrows the run from
        its start; `turn_ccw` turns the line counter-clockwise on the page
        unrotated (new text takes its page's turn, so it reads upright as the
        page is shown). Returns anything that came out other than asked, for the edge
        to put into words; empty when nothing did.
        """
        setting = Setting(span.size if size is None else size, scale_x, turn_ccw)
        return self._writer.draw(span, text, plans=self._plans, setting=setting)

    def keep_pages(self, pages: list[int]) -> list[Message]:
        """Keep only `pages`, in that order: page `pages[0]` becomes the first.

        Call it after the last draw: page numbers change here. Returns what
        came out other than asked.
        """
        every_page_kept = set(pages) == set(range(self._driver.page_count()))
        said = [] if every_page_kept else self._drop_tags()
        self._driver.keep_pages(pages)
        # Looked up and named by page number, and those just changed.
        self._fonts = DocumentFonts(self._driver, google=self._google)
        self._plans = DrawPlanner(self._fonts, self._driver)
        self._writer.forget_pages()
        return said

    def _drop_tags(self) -> list[Message]:
        """Drop the file's tags, and say so if it had any.

        They point at every page, so they would keep left-out pages in the file.
        """
        if not self._driver.has_tags():
            return []
        self._driver.drop_tags()
        return [Message("tags_dropped")]

    def save(self, path: str) -> list[Message]:
        """Write the document to `path`, the fonts we added cut to the letters drawn in them.

        Call it last: afterwards they can't draw any new letter. Returns
        anything that came out other than asked, for the edge to put into words.
        """
        return self._writer.save(path)

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with any word of their text still in their box. A black box won't hide it.

        Part of a card number is as much a leak as all of it. Only each span's
        own box is read, so the same words elsewhere aren't a leak. Spaces are
        ignored, so respacing can't hide a leftover.
        """
        left = (self._left_on(page, on_page) for page, on_page in by_page(spans).items())
        return [span for on_page in left for span in on_page]

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with any word of their text still in their box."""
        texts = self._driver.text_in(page, [span.bbox for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def close(self) -> None:
        """Release the open document."""
        self._driver.close()

    def __enter__(self) -> Engine:
        """Lets the engine be used as `with open_pdf(path) as engine:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the document when the `with` block ends."""
        self.close()


def by_page(spans: Iterable[Span]) -> dict[int, list[Span]]:
    """`spans` grouped by page, each page's in the order given."""
    grouped: dict[int, list[Span]] = {}
    for span in spans:
        grouped.setdefault(span.page, []).append(span)
    return grouped


def field_draws(field: FormField, span: Span) -> bool:
    """Whether `field` draws `span`: it sits inside, and its words are part of the value."""
    words = " ".join(span.text.split())
    shown = " ".join(field.value.split())
    return bool(words) and words in shown and middle_inside(span.bbox, field.box)


def middle_inside(box: Rect, area: Rect) -> bool:
    """Whether the middle of `box` lies inside `area`."""
    x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    return area.x0 <= x <= area.x1 and area.y0 <= y <= area.y1


def any_word_left(text: str, left: str) -> bool:
    """Whether any word of `text`, or all of it, is in `left`, spaces aside.

    A word of one letter doesn't count alone: "A" is in most labels drawn over
    a redaction, such as "[REDACTED]".
    """
    leftover = "".join(left.split())
    words = [word for word in text.split() if len(word) > 1]
    return any(word in leftover for word in [*words, "".join(text.split())])
