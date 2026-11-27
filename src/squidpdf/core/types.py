from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NewType, get_args

import orjson

import squidpdf.core.constants as constants

# Easy to mix up, so mypy keeps them apart.
Codepoint = NewType("Codepoint", int)
GlyphId = NewType("GlyphId", int)  # a shape's place in the font; 0 is the empty .notdef
type GlyphName = str

_LEVEL = (1.0, 0.0)
# A plain alias: pydantic reads it.
QuarterTurn = Literal[0, 90, 180, 270]
QUARTER_TURNS: dict[QuarterTurn, tuple[int, int]] = {
    0: (1, 0),
    90: (0, 1),
    180: (-1, 0),
    270: (0, -1),
}


def turn_added(turn: QuarterTurn, more: QuarterTurn) -> QuarterTurn:
    quarters = list(QUARTER_TURNS)
    return quarters[(quarters.index(turn) + quarters.index(more)) % len(quarters)]


type FontKind = Literal["truetype", "type0", "type1", "type3", "other"]
type FontFileType = Literal["truetype", "opentype", "cff", "type1", "none"]


@dataclass(frozen=True, slots=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def overlaps(self, other: Rect) -> bool:
        across = self.x0 < other.x1 and other.x0 < self.x1
        down = self.y0 < other.y1 and other.y0 < self.y1
        return across and down

    def grown(self, by: float) -> Rect:
        return Rect(self.x0 - by, self.y0 - by, self.x1 + by, self.y1 + by)

    def union(self, other: Rect) -> Rect:
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass(frozen=True, slots=True)
class Page:
    """`turn_cw` is the file's /Rotate: nothing on the server applies it; the browser does."""

    width: float
    height: float
    turn_cw: QuarterTurn


@dataclass(frozen=True, slots=True)
class TextPiece:
    text: str
    font: str
    font_object: int | None
    size: float
    color: tuple[float, float, float]
    opacity: float
    box: Rect
    origin: tuple[float, float]
    direction: tuple[float, float] = _LEVEL


@dataclass(frozen=True, slots=True)
class FormField:
    """The field draws its value itself, so erasing the page's text can't reach it."""

    box: Rect
    value: str


CopyPlace = Literal[
    "title", "metadata", "bookmarks", "comments", "form_fields", "screen_reader_text"
]
COPY_PLACES: tuple[CopyPlace, ...] = get_args(CopyPlace)
HiddenPlace = Literal[CopyPlace, "signatures"]


@dataclass(frozen=True, slots=True)
class HiddenCopy:
    """A screen reader, a search or copy and paste reads it: a redacted word in one leaks."""

    place: CopyPlace
    text: str


@dataclass(frozen=True, slots=True)
class FontResource:
    """A page's drawing names a font by a short name in its resources ("F1")."""

    resource: str
    font_object: int


@dataclass(frozen=True, slots=True)
class TextRun:
    text: str
    origin: tuple[float, float]
    resource: str


@dataclass(frozen=True, slots=True)
class CodeRun:
    codes: bytes
    font_object: int  # its font's PDF object: the page may know it by another resource name


@dataclass(frozen=True, slots=True)
class PageFont:
    font_object: int
    name: str
    kind: FontKind
    file_type: FontFileType
    resource: str
    codes_are_cids: bool
    in_form: bool

    @property
    def is_embedded(self) -> bool:
        return self.file_type != "none"


@dataclass(frozen=True, slots=True)
class Fragment:
    text: str
    bbox: Rect
    origin: tuple[float, float]


@dataclass(frozen=True, slots=True)
class Span:
    id: str
    page: int
    text: str
    font: str
    font_object: int | None
    size: float
    color: tuple[float, float, float]
    opacity: float
    bbox: Rect
    origin: tuple[float, float]
    fragments: tuple[Fragment, ...]
    direction: tuple[float, float] = _LEVEL

    @property
    def merged(self) -> bool:
        return len(self.fragments) > 1

    @property
    def turned(self) -> bool:
        horizontal, vertical = self.direction
        # Upside down has no vertical part, only a leftward one.
        return horizontal < 0 or abs(vertical) > constants.TURN_TOLERANCE


@dataclass(frozen=True, slots=True, kw_only=True)
class RedactedRun(Span):
    before: str
    after: str


def run_of(
    span: Span, *, text: str, box: Rect, before: str = "", after: str = ""
) -> RedactedRun:
    return RedactedRun(
        id=span.id,
        page=span.page,
        text=text,
        font=span.font,
        font_object=span.font_object,
        size=span.size,
        color=span.color,
        opacity=span.opacity,
        bbox=box,
        origin=span.origin,
        fragments=span.fragments,
        direction=span.direction,
        before=before,
        after=after,
    )


def whole_run(span: Span) -> RedactedRun:
    return run_of(span, text=span.text, box=span.bbox)


def same_font(text: TextPiece | Span, other: TextPiece | Span) -> bool:
    return (text.font_object, text.font) == (other.font_object, other.font)


EM = 1000  # widths are given per 1000 em, as PDF font widths are


RestOfLine = Literal["in_place", "moved", "removed"]


@dataclass(frozen=True, slots=True)
class PlacedLetter:
    letter: str
    box: Rect
    origin: tuple[float, float]
    size: float


@dataclass(frozen=True, slots=True)
class DrawnElsewhere:
    taken: list[DrawnGlyph]
    moved: list[DrawnGlyph]
    rests: list[RestOfLine]
    unchecked: bool


@dataclass(frozen=True, slots=True)
class DrawnLine:
    text: str
    origin: tuple[float, float]
    turn_ccw: QuarterTurn


@dataclass(frozen=True, slots=True)
class DrawnGlyph:
    letter: str
    glyph: GlyphId  # as `FontCode.glyph`; -1 for a ligature's later letters
    origin: tuple[float, float]
    width: float
    direction: tuple[float, float]


@dataclass(frozen=True, slots=True)
class FontCode:
    value: int
    letter: str
    glyph: GlyphId
    width: float


@dataclass(frozen=True, slots=True)
class CodedFont:
    code_bytes: int
    letters: dict[str, FontCode] = field(repr=False)
    codes: tuple[FontCode, ...] = field(repr=False)

    def preferring(self, drawn: Mapping[str, FontCode]) -> CodedFont:
        if not drawn:
            return self
        return CodedFont(self.code_bytes, {**self.letters, **drawn}, self.codes)

    def has_alternates(self, text: str) -> bool:
        letters = set(text)
        counted = Counter(code.letter for code in self.codes if code.letter in letters)
        return any(count > 1 for count in counted.values())


SOLID = 1.0

type Style = Literal["regular", "bold", "italic", "bold-italic"]
type Category = Literal["sans", "serif", "mono", "handwriting"]


@dataclass(frozen=True, slots=True)
class Face:
    name: str
    family: str
    style: Style
    category: Category
    file: str
    license: str
    same_widths_as: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LookAlike:
    face: Face
    same_widths: bool


@dataclass(frozen=True, slots=True)
class FontDescriptor:
    flags: int
    weight: float | None
    italic_angle: float
    name: str | None


def new_text(
    page: int,
    *,
    origin: tuple[float, float],
    text: str,
    size: float,
    font: str,
    font_object: int | None = None,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Span:
    x, y = origin
    box = Rect(x, y - size, x, y)
    return Span(
        id="",
        page=page,
        text=text,
        font=font,
        font_object=font_object,
        size=size,
        color=color,
        opacity=SOLID,
        bbox=box,
        origin=origin,
        fragments=(),
    )


@dataclass(frozen=True, slots=True)
class SpanIndex:
    """Always the original file: rebuilt, every id changes and client references dangle."""

    by_id: dict[str, Span] = field(repr=False)

    def __iter__(self) -> Iterator[Span]:
        return iter(self.by_id.values())


def index_of(spans: Iterable[Span]) -> SpanIndex:
    return SpanIndex({span.id: span for span in spans})


def by_page[S: Span](spans: Iterable[S]) -> dict[int, list[S]]:
    grouped: dict[int, list[S]] = {}
    for span in spans:
        grouped.setdefault(span.page, []).append(span)
    return grouped


def asked_by_page[S: Span, T](
    spans: Iterable[S], ask: Callable[[int, list[S]], list[T]]
) -> list[T]:
    return [answer for page, on_page in by_page(spans).items() for answer in ask(page, on_page)]


def index_as_json(index: SpanIndex) -> bytes:
    return orjson.dumps(list(index))


def index_from_json(saved: bytes) -> SpanIndex:
    return index_of(_load_span(span) for span in orjson.loads(saved))


def _load_span(saved: dict[str, Any]) -> Span:
    rebuilt: dict[str, Any] = {
        "color": tuple(saved["color"]),
        "bbox": Rect(**saved["bbox"]),
        "origin": tuple(saved["origin"]),
        "fragments": tuple(_load_fragment(fragment) for fragment in saved["fragments"]),
        "direction": tuple(saved["direction"]),
    }
    return Span(**saved | rebuilt)


def _load_fragment(saved: dict[str, Any]) -> Fragment:
    rebuilt: dict[str, Any] = {"bbox": Rect(**saved["bbox"]), "origin": tuple(saved["origin"])}
    return Fragment(**saved | rebuilt)


def pages_as_json(pages: list[Page]) -> bytes:
    return orjson.dumps(pages)


def pages_from_json(saved: bytes) -> list[Page]:
    return [Page(**page) for page in orjson.loads(saved)]


@dataclass(frozen=True, slots=True)
class Line:
    span_ids: tuple[str, ...]
    room_pt: float


@dataclass(frozen=True, slots=True)
class FreeSpace:
    rooms: dict[str, float] = field(repr=False)
    lines: list[Line] = field(repr=False)


def free_space_as_json(free_space: FreeSpace) -> bytes:
    return orjson.dumps(free_space)


def free_space_from_json(saved: bytes) -> FreeSpace:
    read = orjson.loads(saved)
    lines = [Line(tuple(line["span_ids"]), line["room_pt"]) for line in read["lines"]]
    return FreeSpace(read["rooms"], lines)


# 12 hex chars: thousands of spans at most, nowhere near a collision.
_SPAN_ID_DIGEST_SIZE = 6


def span_id(page: int, bbox: Rect, *, font: str, text: str, ordinal: int) -> str:
    """The ordinal separates spans sharing text, font and a rounded box, as empty cells do."""
    seed = f"{page}:{bbox.x0:.1f}:{bbox.y0:.1f}:{font}:{text}:{ordinal}"
    return hashlib.blake2s(seed.encode(), digest_size=_SPAN_ID_DIGEST_SIZE).hexdigest()
