"""A PDF open for editing: its spans, what we can promise about each, and the edits on it.

The product's own logic, written against a `core.driver.PdfDriver`'s primitives,
so it's the same over any PDF library. Open one with `core.open_pdf`.

The engine speaks only in primitives (remove, draw), so it never learns what a
Replace or a Redact is, which is what keeps `core` free of feature imports.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import groupby

from squidpdf.core import faces
from squidpdf.core.constants import TOLERANCE_PT, TURN_TOLERANCE
from squidpdf.core.document_fonts import DocumentFonts
from squidpdf.core.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.embedded import FontUnusable
from squidpdf.core.fidelity import Fidelity, FidelityReport
from squidpdf.core.fonts import face_bytes, strip_subset, trimmed
from squidpdf.core.google import Fetch, GoogleFontController
from squidpdf.core.message import Message
from squidpdf.core.pooled import CodedStretch, FontCopy, PooledFont, copy_source
from squidpdf.core.spacing import Word, lacks_space, placed_words, span_gaps
from squidpdf.core.spans import build_index
from squidpdf.core.types import (
    SOLID,
    CodedFont,
    Face,
    Page,
    Rect,
    Span,
    SpanIndex,
    TextRun,
)

__all__ = [
    "letter_widths",
    "DrawPlan",
    "PageNames",
    "AddedFont",
    "AddedFonts",
    "Engine",
]

_EM = 1000  # widths are given per 1000 em, as PDF font widths are
_WIDTH_DP = 2  # finer than any page can show
_ALIAS_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids
_PDF_DP = 4  # decimals written into a content stream, far below a device pixel
# Each quarter turn counter-clockwise, as its cosine and sine: exact, not rounded floats.
_QUARTER_TURNS = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def letter_widths(font: FontProgram, letters: Iterable[str]) -> dict[str, float]:
    """Each letter's width in `font`, per 1000 em, as the browser gets it."""
    return {ch: round(font.advance(ch) * _EM, _WIDTH_DP) for ch in letters}


@dataclass(frozen=True, slots=True)
class DrawPlan:
    """How a line is drawn: the font that draws it, and the line as it comes out.

    Worked out once, and read alike by measuring, judging and drawing, so what
    the fit says is what `draw` does.
    """

    drawn_in: PooledFont | Face  # the file's own copies of the font, pooled, or a face we ship
    text: str  # the line as drawn: spelled as that font has it, less the letters left out
    missing: list[str]  # letters the span's own font lacks: its look-alike's, when it has none
    left_out: list[str]  # letters no font we have can draw, each once, in the order typed


@dataclass(frozen=True, slots=True)
class Setting:
    """How a line is set: its size, how much it's narrowed, and how far it's turned."""

    size: float  # in points
    scale_x: float  # 1 is as the font draws it; less narrows each run from its start
    turn: int  # degrees counter-clockwise on the page unrotated: 0, 90, 180 or 270


@dataclass(slots=True)
class PageNames:
    """The name each page gives a font once it's been added there to draw with."""

    # Each copy of a font, by (page, `copy_source`), or why it wasn't added.
    own: dict[tuple[int, str], str | FontUnusable] = field(default_factory=dict)
    # The faces we ship, by (page, face file).
    faces: dict[tuple[int, str], str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AddedFont:
    """A font we added to the document whole: a face we ship, or Google's copy of one."""

    name: str  # what the user reads, and what its drawn letters are kept under
    file: bytes  # the whole font, to cut down on save


@dataclass(slots=True)
class AddedFonts:
    """The fonts added to the document whole, for `save` to cut down."""

    # By font object: pages share one per font.
    by_xref: dict[int, AddedFont] = field(default_factory=dict)
    # Every letter drawn in each, over all pages, by its name.
    drawn: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Stretch:
    """Letters of one word drawn in one font, and where on the line they start."""

    text: str
    offset: float  # from the line's start, in points
    alias: str  # the page's name for the font they're drawn in


class Engine:
    """A PDF open for editing. Use it in a `with`, or close it."""

    def __init__(self, driver: PdfDriver, *, fetch: Fetch | None = None) -> None:
        """Take over an open document, with nothing looked up or added yet.

        `fetch` gets Google's copy of a font; without one, only the file's copies lend.
        """
        self._driver = driver
        self._google = None if fetch is None else GoogleFontController(driver, fetch=fetch)
        self._fonts = DocumentFonts(driver, google=self._google)
        self._names = PageNames()
        self._added = AddedFonts()

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
        plan = self.plan_for(span, span.text)
        drawn_in = plan.drawn_in
        # The file's own font redraws its own text, as it is or not quite.
        if isinstance(drawn_in, PooledFont):
            unlike = self._unlike(span, plan)
            if unlike is None:
                return FidelityReport(span.id, Fidelity.EXACT, span.font, in_file=True)
            return FidelityReport(
                span.id, Fidelity.APPROXIMATE, span.font, in_file=True, why=unlike
            )
        own = self._fonts.own(span)
        match = self._fonts.look_alike(span)
        return FidelityReport(
            span.id,
            Fidelity.SUBSTITUTE,
            span.font,
            in_file=own is not None,
            substitute=drawn_in.name,
            why=self._fonts.why_not(span) if own is None else own.why_missing(span.text),
            same_widths=match.same_widths and drawn_in == match.face,
        )

    def _unlike(self, span: Span, plan: DrawPlan) -> Message | None:
        """How a redraw of the span's own text in its own font looks unlike it; None if not."""
        # Turned on the page: redraws are level.
        _along, rise = span.direction
        if abs(rise) > TURN_TOLERANCE:
            return Message("turned_text")
        # Letters no font we have draws: a redraw leaves them out.
        if plan.left_out:
            return Message("undrawable_letters", {"letters": list(plan.left_out)})
        # New text: its box is only nominal, so there's no spacing of its own to keep.
        if not span.fragments:
            return None
        # Spaced or stretched (letter spacing, scaling, a justified line): redraws close it up.
        redrawn = self._width(span, plan, size=span.size)
        if abs(span.bbox.width - redrawn) > TOLERANCE_PT:
            return Message("spaced_text")
        return None

    def widths(self, span: Span) -> dict[str, float]:
        """Each letter the span's font really draws, and its width per 1000 em.

        The same font `measure` uses, every copy of it in the file pooled, so
        the browser's sum of them agrees with it, with one exception: in a font
        with no space, a space here is the page's usual gap, where `measure`
        keeps the line's own, so a justified line can differ. A trimmed (subset)
        font's emptied letters don't count; if Coverage can't read a copy, the
        library's own list stands in. A font not in the file is drawn in its
        look-alike, whose list is kept to GLYPH_LIST_RANGES.
        """
        pool = self._fonts.own(span)

        # Not in the file: the look-alike draws it.
        if pool is None:
            face = self._fonts.look_alike(span).face
            return letter_widths(self._driver.face_font(face), faces.face_letters(face))

        # Each from the copy that draws it: its width list if written by code, else the font.
        letters = sorted(pool.letters.items())
        widths = {ch: round(copy.widths[ch], _WIDTH_DP) for ch, copy in letters}
        # Written by letter, with no space of its own: a space is the page's usual gap.
        spaceless = pool.own.embedded.coded is None and lacks_space(pool)
        if spaceless:
            widths[" "] = round(self._fonts.usual_gap(span, pool) * _EM, _WIDTH_DP)
        return widths

    def measure(self, span: Span, text: str) -> float:
        """How wide `text` would render, placed as `draw` places it, at this span's size."""
        return self._width(span, self.plan_for(span, text), size=span.size)

    def missing(self, span: Span, text: str) -> list[str]:
        """Characters no copy of this span's font in the file can actually draw.

        Checks each letter draws a shape rather than trusting the font's list: a
        trimmed (subset) font still lists letters whose shapes were emptied. A
        font not in the file is checked against its look-alike, the real file we ship.
        """
        return self.plan_for(span, text).missing

    def left_out(self, span: Span, text: str) -> list[str]:
        """Characters no font we have can draw here, so a redraw leaves them out."""
        return self.plan_for(span, text).left_out

    def stand_in(self, span: Span, text: str) -> str:
        """The face we ship that draws `text` when the span's own font can't: "Carlito Bold"."""
        drawn_in = self.plan_for(span, text).drawn_in
        # The own font draws it: the face that would if the page wouldn't take the font.
        if isinstance(drawn_in, PooledFont):
            return self._stand_in_for(span, text).face.name
        return drawn_in.name

    # Changing it.

    def remove(self, spans: list[Span]) -> None:
        """Delete these spans' text for real, not by covering it with a box.

        One box per span, not per fragment: the cost grows with the box count,
        and a span's box covers its fragments. Lines, underlines and links stay. Each
        span's font and look-alike are read first: erasing can delete a font the
        page no longer uses, and `draw` still needs both. So are the page's gaps.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
            pool = self._fonts.own(span)
            self._fonts.look_alike(span)
            # Erasing takes the gaps a new space is measured against.
            if pool is not None and lacks_space(pool):
                self._fonts.usual_gap(span, pool)

        for page, page_spans in by_page.items():
            self._driver.erase_text(page, [span.bbox for span in page_spans])

    def unlink(self, spans: list[Span]) -> None:
        """Delete every link over these spans: a link can carry the text it's on (a mailto:)."""
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
        for page, page_spans in by_page.items():
            self._driver.drop_links(page, [span.bbox for span in page_spans])

    def draw(
        self,
        span: Span,
        text: str,
        *,
        size: float | None = None,
        scale_x: float = 1.0,
        turn: int = 0,
    ) -> list[Message]:
        """Redraw `text` at the span's baseline, in its own font where the file has it.

        `size` in points replaces the span's own; `scale_x` narrows the run from
        its start. A letter the span's copy of its font lacks comes from another
        copy of the same font in the file, placed as one font would place it. A
        character no copy can draw sends the whole run to the stand-in, so a
        line never mixes two faces, unless no font we have draws it: then it's
        only left out. A font with no space is drawn word by word.
        Returns anything that came out other than asked, for the edge to put
        into words; empty when nothing did. `turn` turns the line counter-clockwise
        on the page unrotated: new text is turned by its page's own turn, so it
        reads upright as the page is shown.
        """
        setting = Setting(span.size if size is None else size, scale_x, turn)
        plan = self.plan_for(span, text)
        drawn_in = plan.drawn_in

        # A face we ship draws the whole line, less what even it can't draw.
        if isinstance(drawn_in, Face):
            return self._draw_in_face(
                span, drawn_in, text=plan.text, left_out=plan.left_out, setting=setting
            )

        # The file's own font, written by code as the original was.
        by_code = coded_in(plan)
        if by_code is not None:
            self._draw_codes(span, by_code, setting=setting)
            return said_left_out(plan.left_out)

        # The file's own copies of the font, by letter, once the page has them.
        try:
            aliases = self._aliases(span.page, drawn_in, plan.text)
        except FontUnusable as problem:  # the page wouldn't take a copy of the font after all
            stand_in = self._stand_in_for(span, text)
            drawn = self._draw_in_face(
                span,
                stand_in.face,
                text=stand_in.text,
                left_out=stand_in.left_out,
                setting=setting,
            )
            return [problem.reason, *drawn]
        self._note_google_letters(drawn_in, plan.text)
        self._write(span, plan.text, font=drawn_in, aliases=aliases, setting=setting)
        return said_left_out(plan.left_out)

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
        self._names = PageNames()
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
        notices: list[Message] = []
        for xref, added in self._added.by_xref.items():
            try:
                font_file = trimmed(added.file, self._added.drawn[added.name])
            except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font)
                # The whole file still draws every letter; the file is only bigger.
                font_file = added.file
                notices.append(Message("face_not_trimmed", {"font": added.name}))
            try:
                self._driver.replace_font_file(xref, font_file)
            except DriverError as problem:  # its file can't be swapped: it stays whole
                notices.append(problem.reason)
        self._driver.save(path)
        return notices

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with any word of their text still in their box. A black box won't hide it.

        Part of a card number is as much a leak as all of it. Only each span's
        own box is read, so the same words elsewhere aren't a leak. Spaces are
        ignored, so respacing can't hide a leftover.
        """
        by_page: dict[int, list[Span]] = {}
        for span in spans:
            by_page.setdefault(span.page, []).append(span)
        left = (self._left_on(page, on_page) for page, on_page in by_page.items())
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

    # What draws a line.

    def plan_for(self, span: Span, text: str) -> DrawPlan:
        """How `text` is drawn at this span: the one answer the fit and the draw share.

        In the file's copies of the span's font if they draw every character;
        otherwise the whole line in the stand-in, less what even that can't
        draw. When the only letters the own font lacks are ones no font we have
        draws, switching would draw none of them, so the own font keeps the
        line without them.
        """
        own = self._fonts.own(span)
        # Not in the file, or unusable: a face we ship draws it, less what even it lacks.
        if own is None:
            composed = unicodedata.normalize("NFC", text)
            look_alike = self._fonts.look_alike(span).face
            missing = faces.face_coverage(look_alike).missing(composed)
            stand_in = faces.stand_in(look_alike, composed)
            return DrawPlan(stand_in.face, stand_in.text, missing, stand_in.left_out)
        text = spelled(own, text)
        missing = own.missing(text)
        if not missing:
            return DrawPlan(own, text, [], [])
        stand_in = self._fonts.stand_in(span, text)
        undrawable = set(missing) <= set(stand_in.left_out)
        if undrawable:
            kept = "".join(ch for ch in text if ch not in missing)
            return DrawPlan(own, kept, missing, missing)
        return DrawPlan(stand_in.face, stand_in.text, missing, stand_in.left_out)

    def _stand_in_for(self, span: Span, text: str) -> faces.StandIn:
        """The face that draws `text` when the span's own font can't, as `plan_for` picks it."""
        composed = unicodedata.normalize("NFC", text)
        return faces.stand_in(self._fonts.look_alike(span).face, composed)

    def _width(self, span: Span, plan: DrawPlan, *, size: float) -> float:
        """How wide `plan`'s line is, placed as `draw` places it, at `size` points."""
        by_code = coded_in(plan)
        # Written by code: widths come from each copy's width list.
        if by_code is not None:
            widths = (
                stretch.coded.letters[ch].width for stretch in by_code for ch in stretch.text
            )
            return sum(widths) * size / _EM
        font = self._program(plan)
        _words, width = self._words(span, plan.text, font=font, size=size)
        return width

    def _program(self, plan: DrawPlan) -> FontProgram:
        """The font program that measures and draws `plan`'s line."""
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, PooledFont):
            return drawn_in
        return self._driver.face_font(drawn_in)

    # Drawing.

    def _words(
        self, span: Span, text: str, *, font: FontProgram, size: float
    ) -> tuple[list[Word], float]:
        """`text` as `draw` places it in `font`, and where the pen ends: its width."""
        one_run = not lacks_space(font) or " " not in text
        if one_run:
            return [Word(text, 0.0)], font.width(text, size)
        # No space to draw: each word goes where the file's gaps put it.
        gaps, usual = span_gaps(span, font), self._fonts.usual_gap(span, font)
        return placed_words(text, font=font, size=size, gaps=gaps, usual=usual)

    def _draw_in_face(
        self, span: Span, face: Face, *, text: str, left_out: list[str], setting: Setting
    ) -> list[Message]:
        """Write `text` at the span's baseline in a face we ship, and say what was left out."""
        alias = self._face_alias(span.page, face)
        self._added.drawn.setdefault(face.name, set()).update(text)
        font = self._driver.face_font(face)
        self._write(span, text, font=font, aliases=dict.fromkeys(text, alias), setting=setting)
        return said_left_out(left_out)

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        aliases: Mapping[str, str],
        setting: Setting,
    ) -> None:
        """Write `text` at the span's baseline, placed by `font`'s widths.

        `aliases` is the page's name for the font each letter is drawn in. A
        run per stretch in one font, each where one font would have put it, in
        reading order, so the text reads back as written.
        """
        x, y = span.origin
        cos, sin = _QUARTER_TURNS[setting.turn]
        # A point's move along the line, narrowed; the page's y grows downward.
        step_x, step_y = cos * setting.scale_x, -sin * setting.scale_x
        words, _width = self._words(span, text, font=font, size=setting.size)
        stretches = stretches_in(words, aliases=aliases, font=font, size=setting.size)
        runs = [
            TextRun(
                stretch.text,
                (x + step_x * stretch.offset, y + step_y * stretch.offset),
                stretch.alias,
            )
            for stretch in stretches
        ]
        self._driver.write_text(
            span.page,
            runs=runs,
            size=setting.size,
            color=span.color,
            opacity=span.opacity,
            scale_x=setting.scale_x,
            turn=setting.turn,
        )

    def _draw_codes(
        self, span: Span, stretches: list[CodedStretch], *, setting: Setting
    ) -> None:
        """Write `text` as codes in the file's own copies of its font, on top of the page.

        One text object: each stretch switches to its copy, and the pen moves on
        by that copy's widths, which agree with the others'.
        """
        x, y = self._driver.to_pdf_space(span.page, span.origin)
        r, g, b = span.color
        shown: list[str] = []
        for stretch in stretches:
            resource = self._resource_for(span.page, stretch)
            codes = hex_codes(stretch.coded, stretch.text)
            shown.append(f"/{resource} {setting.size:.{_PDF_DP}f} Tf <{codes}> Tj")
        # See-through, as the original was.
        paint = ""
        if span.opacity < SOLID:
            paint = f" /{self._driver.add_opacity(span.page, span.opacity)} gs"
        # Where the text goes: narrowed along its line, turned, and placed.
        cos, sin = _QUARTER_TURNS[setting.turn]
        narrow = setting.scale_x
        matrix = [narrow * cos, narrow * sin, -sin, cos, x, y]
        placed = " ".join(f"{number:.{_PDF_DP}f}" for number in matrix)
        # Save the page's settings, set color, place the text, write each stretch
        # in its font and size, then put the settings back.
        stream = (
            f"q{paint} BT {r:.{_PDF_DP}f} {g:.{_PDF_DP}f} {b:.{_PDF_DP}f} rg"
            f" {placed} Tm"
            f" {' '.join(shown)} ET Q"
        )
        self._driver.add_content(span.page, stream.encode())

    def _resource_for(self, page: int, stretch: CodedStretch) -> str:
        """The page's name for a copy written by code, pointed at it before each draw.

        Erasing can drop a font the page no longer uses. The span's own copy
        keeps the name the page gave it; any other gets a fresh one, since
        another page's name for it may mean something else here.
        """
        font = stretch.copy.font
        fresh = page_name("C", f"{font.xref} {font.name}")
        resource = stretch.coded.resource if stretch.own else fresh
        self._driver.restore_font(page, resource, font.xref)
        return resource

    def _aliases(self, page: int, pool: PooledFont, text: str) -> dict[str, str]:
        """The page's name for the copy each letter of `text` is drawn in.

        Raises FontUnusable when the library won't add one of them.
        """
        return {ch: self._alias(page, pool.copy_for(ch)) for ch in dict.fromkeys(text)}

    def _alias(self, page: int, copy: FontCopy) -> str:
        """The page's name for a copy of a font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises FontUnusable when the library won't add it.
        """
        key = (page, copy_source(copy))
        if key not in self._names.own:
            self._names.own[key] = self._add_font(page, copy)
        found = self._names.own[key]
        if isinstance(found, FontUnusable):
            raise FontUnusable(found.reason)
        return found

    def _add_font(self, page: int, copy: FontCopy) -> str | FontUnusable:
        """Add a copy of a font to a page under a new name, or say why it failed.

        Google's copy goes in whole, to be cut down on save like a face we ship.
        """
        # Each copy its own name, by its source: copies of one font share their name.
        name = page_name("F", copy_source(copy))
        try:
            added = self._driver.add_font(page, copy.embedded.file, name=name)
        except DriverError as problem:  # the page won't take it: the stand-in draws instead
            return FontUnusable(problem.reason)
        if copy.google is not None:
            self._added.by_xref[added.xref] = AddedFont(lent_name(copy), copy.embedded.file)
        return added.resource

    def _note_google_letters(self, pool: PooledFont, text: str) -> None:
        """Keep the letters Google's copy draws in `text`, for `save` to cut it down to."""
        for ch in text:
            copy = pool.copy_for(ch)
            if copy.google is not None:
                self._added.drawn.setdefault(lent_name(copy), set()).add(ch)

    def _face_alias(self, page: int, face: Face) -> str:
        """The page's name for a face we ship, added to the page on first use.

        Once per page, as `_alias` does for the file's own fonts.
        """
        key = (page, face.file)
        if key not in self._names.faces:
            name = page_name("S", face.file)
            added = self._driver.add_font(page, face_bytes(face), name=name)
            self._names.faces[key] = added.resource
            self._added.by_xref[added.xref] = AddedFont(face.name, face_bytes(face))
        return self._names.faces[key]


def page_name(kind: str, source: str) -> str:
    """A name for the page's resources, `kind` then a digest of `source`.

    Made from what it names, so it can't clash with a name a file's writer chose.
    """
    digest = hashlib.blake2s(source.encode(), digest_size=_ALIAS_DIGEST_SIZE)
    return kind + digest.hexdigest()


def lent_name(copy: FontCopy) -> str:
    """What the user reads for Google's copy of a font: the font's own name, "Poppins-Bold"."""
    return strip_subset(copy.font.name)


def stretches_in(
    words: Iterable[Word], *, aliases: Mapping[str, str], font: FontProgram, size: float
) -> list[Stretch]:
    """Each word split where the font its letters are drawn in changes, in order.

    `aliases` is the page's name for the font each letter is drawn in.
    """
    return [
        stretch
        for word in words
        for stretch in word_stretches(word, aliases=aliases, font=font, size=size)
    ]


def word_stretches(
    word: Word, *, aliases: Mapping[str, str], font: FontProgram, size: float
) -> list[Stretch]:
    """One word split where its font changes, each stretch starting where the last ends."""
    stretches: list[Stretch] = []
    start = word.offset
    for alias, letters in groupby(word.text, key=lambda ch: aliases[ch]):
        text = "".join(letters)
        stretches.append(Stretch(text, start, alias))
        start += font.width(text, size)
    return stretches


def hex_codes(coded: CodedFont, text: str) -> str:
    """`text` as the font's codes, in hex, each as many bytes wide as the font's."""
    hex_digits = coded.code_bytes * 2
    return "".join(f"{coded.letters[ch].value:0{hex_digits}x}" for ch in text)


def spelled(own: PooledFont, text: str) -> str:
    """`text` spelled as the span's own font has it: as typed, composed or in pieces.

    é can be one letter or e and an accent. A trimmed font keeps whichever
    its document used, and macOS pastes in pieces. With none whole in the
    font, composed: the faces we ship draw it that way.
    """
    composed = unicodedata.normalize("NFC", text)
    in_pieces = unicodedata.normalize("NFD", text)
    whole = (spelling for spelling in (text, composed, in_pieces) if not own.missing(spelling))
    return next(whole, composed)


def unspaced(text: str) -> str:
    """`text` with every space, tab and line break taken out."""
    return "".join(text.split())


def any_word_left(text: str, left: str) -> bool:
    """Whether any word of `text` is in `left`, spaces aside."""
    leftover = unspaced(left)
    return any(word in leftover for word in text.split())


def said_left_out(letters: list[str]) -> list[Message]:
    """The notice a draw gives for letters it left out; none when it left none."""
    return [Message("left_out", {"letters": list(letters)})] if letters else []


def coded_in(plan: DrawPlan) -> list[CodedStretch] | None:
    """`plan`'s line in the codes of the file's copies of its font, a stretch per copy.

    None unless they draw it by code.
    """
    drawn_in = plan.drawn_in
    # A face we ship, or the file's copies written by letter: no codes.
    if isinstance(drawn_in, Face) or drawn_in.own.embedded.coded is None:
        return None
    return drawn_in.coded_stretches(plan.text)
