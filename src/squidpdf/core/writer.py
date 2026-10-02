"""Drawing a line in the font its plan names, and cutting the fonts we added down on save.

A font is added to a page once, under a resource name (see
`core.types.FontResource`) made from what it is, and every line drawn in it
there uses that name.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from itertools import groupby
from typing import assert_never

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

from squidpdf.core.app.message import Message
from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.fonts.embedded import FontUnusable, made_once, remembered
from squidpdf.core.fonts.pool import CodedRun, FontCopy, PooledFont, copy_source
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.plan import DrawPlanner, coded_in
from squidpdf.core.text.spacing import Word
from squidpdf.core.types import (
    QUARTER_TURNS,
    CodedFont,
    CodeRun,
    Face,
    QuarterTurn,
    Span,
    TextRun,
)

_NAME_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids


@dataclass(frozen=True, slots=True)
class Setting:
    """How a line is set: its size, how much it's narrowed, and how far it's turned."""

    size: float  # in points
    scale_x: float  # 1 is as the font draws it; less narrows each run from its start
    turn_ccw: QuarterTurn  # on the page unrotated


@dataclass(slots=True)
class _PageNames:
    """The resource name each page gives a font once it's been added there to draw with."""

    # Each copy of a font, by (page, `copy_source`), or why it wasn't added.
    own: dict[tuple[int, str], str | FontUnusable] = field(default_factory=dict)
    # The faces we ship, by (page, face file).
    faces: dict[tuple[int, str], str] = field(default_factory=dict)

    def own_resource(self, key: tuple[int, str], add: Callable[[], str]) -> str | FontUnusable:
        """The resource name of a copy of a font on a page, added on first use, or why not."""
        return remembered(self.own, key, add)

    def face_resource(self, key: tuple[int, str], add: Callable[[], str]) -> str:
        """The resource name of a face we ship on a page, added on first use."""
        return made_once(self.faces, key, add)

    def forget(self) -> None:
        """Forget every page's names, as a fresh record: the pages were renumbered."""
        self.own.clear()
        self.faces.clear()


@dataclass(slots=True)
class _AddedFont:
    """A font we added to the document whole: a face we ship, or a copy lent from outside.

    Cut down on save to the letters drawn in it.
    """

    name: str  # what the user reads it as
    file: bytes = field(repr=False)  # the whole font
    xrefs: set[int] = field(default_factory=set)  # its PDF objects; pages usually share one
    # Every letter drawn in it, over all pages.
    drawn: set[str] = field(default_factory=set, repr=False)

    def note_drawn(self, text: str) -> None:
        """Keep the letters of `text`, drawn in it, for `save` to cut it down to."""
        self.drawn.update(text)

    def note_object(self, xref: int) -> None:
        """Keep `xref`, another PDF object it was added as: another page's, usually."""
        self.xrefs.add(xref)


@dataclass(frozen=True, slots=True)
class _OffsetRun:
    """A run of a line: letters of one word drawn in one font, and where on it they start."""

    text: str
    offset: float  # from the line's start, in points
    resource: str  # the resource name of the font they're drawn in


@dataclass(frozen=True, slots=True, eq=False)
class PageWriter:
    """Draws lines onto a document's pages, adding each font a page needs once.

    Keeps the fonts added whole, by where each file came from, for `save` to
    cut down.
    """

    driver: PdfDriver
    names: _PageNames = field(default_factory=_PageNames)
    # By the face's file, or Google's source for its copy: one font file each.
    added: dict[str, _AddedFont] = field(default_factory=dict, repr=False)

    def draw(
        self, span: Span, text: str, *, plans: DrawPlanner, setting: Setting
    ) -> list[Message]:
        """Draw `text` at the span's baseline as planned, and say what came out otherwise."""
        plan = plans.plan_for(span, text)
        by_code = coded_in(plan)
        drawn_in = plan.drawn_in
        # A face we ship draws the whole line, less what even it can't draw.
        if isinstance(drawn_in, Face):
            self._write_in_face(span, drawn_in, text=plan.text, plans=plans, setting=setting)
            return _said_left_out(plan.left_out)
        # The file's own font, written by code as the original was.
        if isinstance(drawn_in, PooledFont) and by_code is not None:
            self._write_codes(span, by_code, setting=setting)
            return _said_left_out(plan.left_out)
        # The file's own copies of the font, by letter, once the page has them.
        if isinstance(drawn_in, PooledFont):
            try:
                resources = self._resources(span.page, drawn_in, plan.text)
            except FontUnusable as problem:  # the page wouldn't take a copy after all
                substitute = plans.substitute_for(span, text)
                self._write_in_face(
                    span, substitute.face, text=substitute.text, plans=plans, setting=setting
                )
                return [problem.reason, *_said_left_out(substitute.left_out)]
            self._note_lent_letters(drawn_in, plan.text)
            self._write(
                span,
                plan.text,
                font=drawn_in,
                resources=resources,
                plans=plans,
                setting=setting,
            )
            return _said_left_out(plan.left_out)
        assert_never(drawn_in)

    def forget_pages(self) -> None:
        """Forget each page's resource names: the pages were just renumbered."""
        self.names.forget()

    def save(self, path: str) -> list[Message]:
        """Write the document to `path`, the fonts we added cut to the letters drawn in them.

        Call it last: afterwards they can't draw any new letter. Returns
        anything that came out other than asked, for the edge to put into words.
        """
        notices: list[Message] = []
        for added in self.added.values():
            try:
                font_file = _trimmed(added.file, added.drawn)
            except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font)
                # The whole file still draws every letter; the file is only bigger.
                font_file = added.file
                notices.append(Message("face_not_trimmed", {"font": added.name}))
            for xref in sorted(added.xrefs):
                try:
                    self.driver.replace_font_file(xref, font_file)
                except DriverError as problem:  # its file can't be swapped: it stays whole
                    notices.append(problem.reason)
        self.driver.save(path)
        return notices

    def _write_in_face(
        self, span: Span, face: Face, *, text: str, plans: DrawPlanner, setting: Setting
    ) -> None:
        """Write `text` at the span's baseline in a face we ship."""
        resource = self._face_resource(span.page, face)
        self.added[face.file].note_drawn(text)
        font = self.driver.face_font(face)
        resources = dict.fromkeys(text, resource)
        self._write(span, text, font=font, resources=resources, plans=plans, setting=setting)

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        resources: Mapping[str, str],
        plans: DrawPlanner,
        setting: Setting,
    ) -> None:
        """Write `text` at the span's baseline, placed by `font`'s widths.

        `resources` is the resource name of the font each letter is drawn in. A
        run per font change, each where one font would have put it, in reading
        order, so the text reads back as written.
        """
        x, y = span.origin
        cos, sin = QUARTER_TURNS[setting.turn_ccw]
        # A point's move along the line, narrowed; the page's y grows downward.
        step_x, step_y = cos * setting.scale_x, -sin * setting.scale_x
        words, _width = plans.words_of(span, text, font=font, size=setting.size)
        offset_runs = _runs_in(words, resources=resources, font=font, size=setting.size)
        runs = [
            TextRun(run.text, (x + step_x * run.offset, y + step_y * run.offset), run.resource)
            for run in offset_runs
        ]
        self.driver.write_text(
            span.page,
            runs=runs,
            size=setting.size,
            color=span.color,
            opacity=span.opacity,
            scale_x=setting.scale_x,
            turn_ccw=setting.turn_ccw,
        )

    def _write_codes(self, span: Span, coded_runs: list[CodedRun], *, setting: Setting) -> None:
        """Write the line as codes in the file's own copies of its font, on top of the page.

        Each run in its copy, the pen moving on by that copy's widths, which
        agree with the others'.
        """
        runs = [
            CodeRun(_codes_for(run.coded, run.text), run.copy.font.xref) for run in coded_runs
        ]
        self.driver.write_codes(
            span.page,
            origin=span.origin,
            runs=runs,
            size=setting.size,
            color=span.color,
            opacity=span.opacity,
            scale_x=setting.scale_x,
            turn_ccw=setting.turn_ccw,
        )

    def _resources(self, page: int, own: PooledFont, text: str) -> dict[str, str]:
        """The resource name of the copy each letter of `text` is drawn in.

        Raises FontUnusable when the library won't add one of them.
        """
        return {ch: self._copy_resource(page, own.copy_for(ch)) for ch in dict.fromkeys(text)}

    def _copy_resource(self, page: int, copy: FontCopy) -> str:
        """The resource name of a copy of a font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises FontUnusable when the library won't add it.
        """
        key = (page, copy_source(copy))
        found = self.names.own_resource(key, partial(self._add_copy, page, copy))
        if isinstance(found, FontUnusable):
            raise FontUnusable(found.reason)
        return found

    def _add_copy(self, page: int, copy: FontCopy) -> str:
        """Add a copy of a font to a page, and return its resource name.

        Raises FontUnusable when the page won't take it. A copy lent from outside
        the file goes in whole, to be cut down on save like a face we ship.
        """
        # Named by its source, so copies of one font are told apart.
        resource = _resource_name("F", copy_source(copy))
        try:
            font_resource = self.driver.add_font(page, copy.embedded.file, resource=resource)
        except DriverError as problem:  # the page won't take it: the substitute draws instead
            raise FontUnusable(problem.reason) from problem
        if copy.lent is not None:
            added_font = _AddedFont(copy.lent.name, copy.embedded.file)
            self._keep_whole(copy.lent.source, added_font, xref=font_resource.xref)
        return font_resource.resource

    def _note_lent_letters(self, own: PooledFont, text: str) -> None:
        """Keep the letters a lent copy draws in `text`, for `save` to cut it down to."""
        for ch in text:
            copy = own.copy_for(ch)
            if copy.lent is not None:
                self.added[copy.lent.source].note_drawn(ch)

    def _face_resource(self, page: int, face: Face) -> str:
        """The resource name of a face we ship, added to the page on first use.

        Once per page, as `_copy_resource` does for the file's own fonts.
        """
        return self.names.face_resource((page, face.file), partial(self._add_face, page, face))

    def _add_face(self, page: int, face: Face) -> str:
        """Add a face we ship to a page, whole, and return its resource name."""
        font_file = face_bytes(face)
        font_resource = self.driver.add_font(
            page, font_file, resource=_resource_name("S", face.file)
        )
        self._keep_whole(face.file, _AddedFont(face.name, font_file), xref=font_resource.xref)
        return font_resource.resource

    def _keep_whole(self, source: str, font: _AddedFont, *, xref: int) -> None:
        """Note a font added whole as font `xref`, for `save` to cut down.

        `source` tells one font file from another: two can share a name.
        """
        # The first time a file is added makes its record; later pages add their object.
        self.added.setdefault(source, font).note_object(xref)


def _resource_name(kind: str, source: str) -> str:
    """A resource name for a font we add, `kind` then a digest of `source`.

    Made from what it names, so it can't clash with a name a file's writer chose.
    """
    digest = hashlib.blake2s(source.encode(), digest_size=_NAME_DIGEST_SIZE)
    return kind + digest.hexdigest()


def _runs_in(
    words: Iterable[Word], *, resources: Mapping[str, str], font: FontProgram, size: float
) -> list[_OffsetRun]:
    """Each word split where the font its letters are drawn in changes, in order.

    `resources` is the resource name of the font each letter is drawn in.
    """
    return [
        run
        for word in words
        for run in _word_runs(word, resources=resources, font=font, size=size)
    ]


def _word_runs(
    word: Word, *, resources: Mapping[str, str], font: FontProgram, size: float
) -> list[_OffsetRun]:
    """One word split where its font changes, each run starting where the last ends."""
    runs: list[_OffsetRun] = []
    start = word.offset
    for resource, letters in groupby(word.text, key=lambda ch: resources[ch]):
        text = "".join(letters)
        runs.append(_OffsetRun(text, start, resource))
        start += font.width(text, size)
    return runs


def _codes_for(coded: CodedFont, text: str) -> bytes:
    """`text` as the font's codes, each as many bytes wide as the font's codes take."""
    return b"".join(coded.letters[ch].value.to_bytes(coded.code_bytes) for ch in text)


def _said_left_out(letters: list[str]) -> list[Message]:
    """The notice a draw gives for letters it left out; none when it left none."""
    return [Message("left_out", {"letters": list(letters)})] if letters else []


def _trimmed(font_file: bytes, letters: Iterable[str]) -> bytes:
    """A font file we added whole, cut down to `letters`."""
    options = Options(
        hinting=True,  # keeps small text crisp on screen, for a few KB
        layout_features=[],  # the PDF places each letter itself: no ligatures or kerning
        retain_gids=True,  # text already on the page points at its glyphs by number
        # FontForge's timestamps: nothing draws with them, and fontTools can't cut them.
        drop_tables=[*Options().drop_tables, "FFTM"],
    )
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=[ord(ch) for ch in letters])
    font = TTFont(io.BytesIO(font_file))
    subsetter.subset(font)
    cut = io.BytesIO()
    font.save(cut)
    return cut.getvalue()
