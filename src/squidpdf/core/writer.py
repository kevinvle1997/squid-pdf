"""Drawing a line in the font its plan names, and cutting the fonts we added down on save.

A page's drawing never names a font by its own name. It uses a short name the
page lists in its resources ("F1"): the font's resource name. A font is added
to a page once, under a resource name made from what it is, and every line
drawn in it there uses that name.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import groupby

from squidpdf.core.driver import DriverError, FontProgram, PdfDriver
from squidpdf.core.embedded import FontUnusable
from squidpdf.core.fonts import face_bytes, strip_subset, trimmed
from squidpdf.core.message import Message
from squidpdf.core.plan import LinePlanner, coded_in
from squidpdf.core.pooled import CodedStretch, FontCopy, PooledFont, copy_source
from squidpdf.core.spacing import Word
from squidpdf.core.types import QUARTER_TURNS, CodedFont, CodeRun, Face, Span, TextRun

__all__ = [
    "Setting",
    "PageNames",
    "AddedFont",
    "AddedFonts",
    "PageWriter",
]

_NAME_DIGEST_SIZE = 6  # bytes -> 12 hex chars, as for span ids


@dataclass(frozen=True, slots=True)
class Setting:
    """How a line is set: its size, how much it's narrowed, and how far it's turned."""

    size: float  # in points
    scale_x: float  # 1 is as the font draws it; less narrows each run from its start
    turn: int  # degrees counter-clockwise on the page unrotated: 0, 90, 180 or 270


@dataclass(slots=True)
class PageNames:
    """The resource name each page gives a font once it's been added there to draw with."""

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
    resource: str  # the resource name of the font they're drawn in


class PageWriter:
    """Draws lines onto a document's pages, adding each font a page needs once.

    Keeps the fonts added whole, for `save` to cut down.
    """

    def __init__(self, driver: PdfDriver) -> None:
        """Write through `driver`, with nothing added yet."""
        self._driver = driver
        self._names = PageNames()
        self._added = AddedFonts()

    def draw(
        self, span: Span, text: str, *, plans: LinePlanner, setting: Setting
    ) -> list[Message]:
        """Draw `text` at the span's baseline as planned, and say what came out otherwise."""
        plan = plans.plan_for(span, text)
        by_code = coded_in(plan)
        match plan.drawn_in:
            # A face we ship draws the whole line, less what even it can't draw.
            case Face() as face:
                self._write_in_face(span, face, text=plan.text, plans=plans, setting=setting)
            # The file's own font, written by code as the original was.
            case PooledFont() if by_code is not None:
                self._write_codes(span, by_code, setting=setting)
            # The file's own copies of the font, by letter, once the page has them.
            case PooledFont() as pool:
                try:
                    resources = self._resources(span.page, pool, plan.text)
                except FontUnusable as problem:  # the page wouldn't take a copy after all
                    stand_in = plans.stand_in_for(span, text)
                    self._write_in_face(
                        span, stand_in.face, text=stand_in.text, plans=plans, setting=setting
                    )
                    return [problem.reason, *said_left_out(stand_in.left_out)]
                self._note_google_letters(pool, plan.text)
                self._write(
                    span,
                    plan.text,
                    font=pool,
                    resources=resources,
                    plans=plans,
                    setting=setting,
                )
        return said_left_out(plan.left_out)

    def forget_pages(self) -> None:
        """Forget each page's resource names: the pages were just renumbered."""
        self._names = PageNames()

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

    def _write_in_face(
        self, span: Span, face: Face, *, text: str, plans: LinePlanner, setting: Setting
    ) -> None:
        """Write `text` at the span's baseline in a face we ship."""
        resource = self._face_resource(span.page, face)
        self._added.drawn.setdefault(face.name, set()).update(text)
        font = plans.driver.face_font(face)
        resources = dict.fromkeys(text, resource)
        self._write(span, text, font=font, resources=resources, plans=plans, setting=setting)

    def _write(
        self,
        span: Span,
        text: str,
        *,
        font: FontProgram,
        resources: Mapping[str, str],
        plans: LinePlanner,
        setting: Setting,
    ) -> None:
        """Write `text` at the span's baseline, placed by `font`'s widths.

        `resources` is the resource name of the font each letter is drawn in. A
        run per stretch in one font, each where one font would have put it, in
        reading order, so the text reads back as written.
        """
        x, y = span.origin
        cos, sin = QUARTER_TURNS[setting.turn]
        # A point's move along the line, narrowed; the page's y grows downward.
        step_x, step_y = cos * setting.scale_x, -sin * setting.scale_x
        words, _width = plans.words_of(span, text, font=font, size=setting.size)
        stretches = stretches_in(words, resources=resources, font=font, size=setting.size)
        runs = [
            TextRun(
                stretch.text,
                (x + step_x * stretch.offset, y + step_y * stretch.offset),
                stretch.resource,
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

    def _write_codes(
        self, span: Span, stretches: list[CodedStretch], *, setting: Setting
    ) -> None:
        """Write the line as codes in the file's own copies of its font, on top of the page.

        Each stretch in its copy, the pen moving on by that copy's widths, which
        agree with the others'.
        """
        runs = [
            CodeRun(codes_for(stretch.coded, stretch.text), stretch.copy.font.xref)
            for stretch in stretches
        ]
        self._driver.write_codes(
            span.page,
            origin=span.origin,
            runs=runs,
            size=setting.size,
            color=span.color,
            opacity=span.opacity,
            scale_x=setting.scale_x,
            turn=setting.turn,
        )

    def _resources(self, page: int, pool: PooledFont, text: str) -> dict[str, str]:
        """The resource name of the copy each letter of `text` is drawn in.

        Raises FontUnusable when the library won't add one of them.
        """
        return {ch: self._copy_resource(page, pool.copy_for(ch)) for ch in dict.fromkeys(text)}

    def _copy_resource(self, page: int, copy: FontCopy) -> str:
        """The resource name of a copy of a font, added to the page on first use.

        Once per page, not per span: a copy per span piled up and slowed every
        redraw. Raises FontUnusable when the library won't add it.
        """
        key = (page, copy_source(copy))
        if key not in self._names.own:
            self._names.own[key] = self._add_copy(page, copy)
        found = self._names.own[key]
        if isinstance(found, FontUnusable):
            raise FontUnusable(found.reason)
        return found

    def _add_copy(self, page: int, copy: FontCopy) -> str | FontUnusable:
        """Add a copy of a font to a page, and return its resource name, or why it failed.

        Google's copy goes in whole, to be cut down on save like a face we ship.
        """
        # Named by its source, so copies of one font are told apart.
        name = resource_name("F", copy_source(copy))
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

    def _face_resource(self, page: int, face: Face) -> str:
        """The resource name of a face we ship, added to the page on first use.

        Once per page, as `_copy_resource` does for the file's own fonts.
        """
        key = (page, face.file)
        if key not in self._names.faces:
            font_file = face_bytes(face)
            added = self._driver.add_font(page, font_file, name=resource_name("S", face.file))
            self._names.faces[key] = added.resource
            self._added.by_xref[added.xref] = AddedFont(face.name, font_file)
        return self._names.faces[key]


def resource_name(kind: str, source: str) -> str:
    """A resource name for a font we add, `kind` then a digest of `source`.

    Made from what it names, so it can't clash with a name a file's writer chose.
    """
    digest = hashlib.blake2s(source.encode(), digest_size=_NAME_DIGEST_SIZE)
    return kind + digest.hexdigest()


def lent_name(copy: FontCopy) -> str:
    """What the user reads for Google's copy of a font: the font's own name, "Poppins-Bold"."""
    return strip_subset(copy.font.name)


def stretches_in(
    words: Iterable[Word], *, resources: Mapping[str, str], font: FontProgram, size: float
) -> list[Stretch]:
    """Each word split where the font its letters are drawn in changes, in order.

    `resources` is the resource name of the font each letter is drawn in.
    """
    return [
        stretch
        for word in words
        for stretch in word_stretches(word, resources=resources, font=font, size=size)
    ]


def word_stretches(
    word: Word, *, resources: Mapping[str, str], font: FontProgram, size: float
) -> list[Stretch]:
    """One word split where its font changes, each stretch starting where the last ends."""
    stretches: list[Stretch] = []
    start = word.offset
    for resource, letters in groupby(word.text, key=lambda ch: resources[ch]):
        text = "".join(letters)
        stretches.append(Stretch(text, start, resource))
        start += font.width(text, size)
    return stretches


def codes_for(coded: CodedFont, text: str) -> bytes:
    """`text` as the font's codes, each as many bytes wide as the font's codes take."""
    return b"".join(coded.letters[ch].value.to_bytes(coded.code_bytes) for ch in text)


def said_left_out(letters: list[str]) -> list[Message]:
    """The notice a draw gives for letters it left out; none when it left none."""
    return [Message("left_out", {"letters": list(letters)})] if letters else []
