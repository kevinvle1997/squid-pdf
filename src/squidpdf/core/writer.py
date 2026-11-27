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
from squidpdf.core.fonts.embedded import FontUnusable, made_once, recalled
from squidpdf.core.fonts.pool import CodedRun, FontCopy, PooledFont, copy_source
from squidpdf.core.pdf.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.plan import DrawPlan, DrawPlanner, coded_in
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

_NAME_DIGEST_SIZE = 6
# A PDF places each letter itself, so a shaper's tables are never asked.
_LAYOUT_TABLES = ("GSUB", "GPOS", "GDEF")


@dataclass(frozen=True, slots=True)
class Setting:
    size: float
    scale_x: float
    turn_ccw: QuarterTurn
    letter_spacing: float


@dataclass(slots=True)
class _PageNames:
    own: dict[tuple[int, str], str | FontUnusable] = field(default_factory=dict)
    faces: dict[tuple[int, str], str] = field(default_factory=dict)

    def own_resource(self, key: tuple[int, str], add: Callable[[], str]) -> str:
        return recalled(self.own, key, add)

    def face_resource(self, key: tuple[int, str], add: Callable[[], str]) -> str:
        return made_once(self.faces, key, add)

    def forget(self) -> None:
        self.own.clear()
        self.faces.clear()


@dataclass(slots=True)
class _AddedFont:
    name: str
    file: bytes = field(repr=False)
    font_objects: set[int] = field(default_factory=set)
    drawn: set[str] = field(default_factory=set, repr=False)

    def note_drawn(self, text: str) -> None:
        self.drawn.update(text)

    def note_object(self, font_object: int) -> None:
        self.font_objects.add(font_object)


@dataclass(frozen=True, slots=True)
class _OffsetRun:
    text: str
    offset: float
    resource: str


@dataclass(frozen=True, slots=True)
class DrawReport:
    face: Face | None
    notices: list[Message]


@dataclass(frozen=True, slots=True, eq=False)
class PageWriter:
    driver: PdfDriver
    plans: DrawPlanner
    names: _PageNames = field(default_factory=_PageNames)
    added: dict[str, _AddedFont] = field(default_factory=dict, repr=False)

    def draw(self, span: Span, text: str, *, plan: DrawPlan, setting: Setting) -> DrawReport:
        by_code = coded_in(plan)
        drawn_in = plan.drawn_in
        if isinstance(drawn_in, Face):
            self._write_in_face(span, drawn_in, text=plan.text, setting=setting)
            return DrawReport(drawn_in, _said_left_out(plan.left_out))
        if isinstance(drawn_in, PooledFont) and by_code is not None:
            self._write_codes(span, by_code, setting=setting)
            return DrawReport(None, _said_left_out(plan.left_out))
        if isinstance(drawn_in, PooledFont):
            return self._draw_by_letter(span, drawn_in, text=text, plan=plan, setting=setting)
        assert_never(drawn_in)

    def _draw_by_letter(
        self, span: Span, own: PooledFont, *, text: str, plan: DrawPlan, setting: Setting
    ) -> DrawReport:
        try:
            resources = self._resources(span.page, own, plan.text)
        except FontUnusable as problem:
            substitute = self.plans.substitute_for(span, text)
            self._write_in_face(span, substitute.face, text=substitute.text, setting=setting)
            notices = [problem.reason, *_said_left_out(substitute.left_out)]
            return DrawReport(substitute.face, notices)
        self._note_lent_letters(own, plan.text)
        self._write(span, plan.text, font=own, resources=resources, setting=setting)
        return DrawReport(None, _said_left_out(plan.left_out))

    def forget_pages(self) -> None:
        self.names.forget()

    def save(self, path: str) -> list[Message]:
        notices: list[Message] = []
        for added in self.added.values():
            try:
                font_file = _trimmed(added.file, added.drawn)
            except Exception:  # noqa: BLE001 (fontTools can fail in many ways on a font)
                # The whole file still draws every letter; the file is only bigger.
                font_file = added.file
                notices.append(Message("face_not_trimmed", {"font": added.name}))
            for font_object in sorted(added.font_objects):
                try:
                    self.driver.replace_font_file(font_object, font_file)
                except DriverError as problem:  # its file can't be swapped: it stays whole
                    notices.append(problem.reason)
        self.driver.save(path)
        return notices

    def _write_in_face(self, span: Span, face: Face, *, text: str, setting: Setting) -> None:
        resource = self._face_resource(span.page, face)
        self.added[face.file].note_drawn(text)
        font = self.driver.face_font(face)
        resources = dict.fromkeys(text, resource)
        self._write(span, text, font=font, resources=resources, setting=setting)

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        resources: Mapping[str, str],
        setting: Setting,
    ) -> None:
        """A run per font change, in reading order, so the text reads back as written."""
        x, y = span.origin
        cos, sin = QUARTER_TURNS[setting.turn_ccw]
        step_x, step_y = cos * setting.scale_x, -sin * setting.scale_x
        size, spacing = setting.size, setting.letter_spacing
        words, _width = self.plans.words_of(span, text, font=font, size=size, spacing=spacing)
        offset_runs = _runs_in(words, resources=resources, font=font, setting=setting)
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
            letter_spacing=setting.letter_spacing,
        )

    def _write_codes(self, span: Span, coded_runs: list[CodedRun], *, setting: Setting) -> None:
        runs = [
            CodeRun(_codes_for(run.coded, run.text), run.copy.font.font_object)
            for run in coded_runs
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
            letter_spacing=setting.letter_spacing,
        )

    def _resources(self, page: int, own: PooledFont, text: str) -> dict[str, str]:
        return {ch: self._copy_resource(page, own.copy_for(ch)) for ch in dict.fromkeys(text)}

    def _copy_resource(self, page: int, copy: FontCopy) -> str:
        """Once per page, not per span: a copy per span piled up and slowed every redraw."""
        key = (page, copy_source(copy))
        return self.names.own_resource(key, partial(self._add_copy, page, copy))

    def _add_copy(self, page: int, copy: FontCopy) -> str:
        resource = _resource_name("F", copy_source(copy))
        try:
            font_resource = self.driver.add_font(page, copy.embedded.file, resource=resource)
        except DriverError as problem:
            raise FontUnusable(problem.reason) from problem
        if copy.lent is not None:
            added_font = _AddedFont(copy.lent.name, copy.embedded.file)
            self._keep_whole(
                copy.lent.source, added_font, font_object=font_resource.font_object
            )
        return font_resource.resource

    def _note_lent_letters(self, own: PooledFont, text: str) -> None:
        for ch in text:
            copy = own.copy_for(ch)
            if copy.lent is not None:
                self.added[copy.lent.source].note_drawn(ch)

    def _face_resource(self, page: int, face: Face) -> str:
        return self.names.face_resource((page, face.file), partial(self._add_face, page, face))

    def _add_face(self, page: int, face: Face) -> str:
        font_file = face_bytes(face)
        font_resource = self.driver.add_font(
            page, font_file, resource=_resource_name("S", face.file)
        )
        self._keep_whole(
            face.file, _AddedFont(face.name, font_file), font_object=font_resource.font_object
        )
        return font_resource.resource

    def _keep_whole(self, source: str, font: _AddedFont, *, font_object: int) -> None:
        self.added.setdefault(source, font).note_object(font_object)


def _resource_name(kind: str, source: str) -> str:
    """Made from what it names, so it can't clash with a name a file's writer chose."""
    digest = hashlib.blake2s(source.encode(), digest_size=_NAME_DIGEST_SIZE)
    return kind + digest.hexdigest()


def _runs_in(
    words: Iterable[Word], *, resources: Mapping[str, str], font: FontProgram, setting: Setting
) -> list[_OffsetRun]:
    return [
        run
        for word in words
        for run in _word_runs(word, resources=resources, font=font, setting=setting)
    ]


def _word_runs(
    word: Word, *, resources: Mapping[str, str], font: FontProgram, setting: Setting
) -> list[_OffsetRun]:
    runs: list[_OffsetRun] = []
    start = word.offset
    for resource, letters in groupby(word.text, key=lambda ch: resources[ch]):
        text = "".join(letters)
        runs.append(_OffsetRun(text, start, resource))
        start += font.width(text, setting.size) + setting.letter_spacing * len(text)
    return runs


def _codes_for(coded: CodedFont, text: str) -> bytes:
    return b"".join(coded.letters[ch].value.to_bytes(coded.code_bytes) for ch in text)


def _said_left_out(letters: list[str]) -> list[Message]:
    return [Message("left_out", {"letters": list(letters)})] if letters else []


def _trimmed(font_file: bytes, letters: Iterable[str]) -> bytes:
    options = Options(
        hinting=True,  # keeps small text crisp on screen, for a few KB
        layout_features=[],  # the PDF places each letter itself: no ligatures or kerning
        retain_gids=True,  # text already on the page points at its glyphs by number
        # FontForge's timestamps, which fontTools can't cut, and the unused layout tables.
        drop_tables=[*Options().drop_tables, "FFTM", *_LAYOUT_TABLES],
    )
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=[ord(ch) for ch in letters])
    font = TTFont(io.BytesIO(font_file))
    subsetter.subset(font)
    cut = io.BytesIO()
    font.save(cut)
    return cut.getvalue()
