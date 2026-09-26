"""What the PDF says. Facts only.

A Span holds what the file states and nothing we concluded. Whether an edit here
will look identical depends on the font library we happen to ship, which is a
judgement that can change without the document changing, so it lives in
`core.fidelity`, not here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, NewType

# Three numbers-or-names about a font that are easy to mix up, so mypy keeps them apart.
Codepoint = NewType("Codepoint", int)  # a letter's Unicode number: 65 is "A"
GlyphId = NewType("GlyphId", int)  # a shape's place in the font; 0 is the empty .notdef
type GlyphName = str  # a shape's name in the font, e.g. "A" or "eacute"


@dataclass(frozen=True, slots=True)
class Rect:
    """A box on the page: left, top, right, bottom, in points."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        """How wide the box is."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """How tall the box is."""
        return self.y1 - self.y0

    def union(self, other: Rect) -> Rect:
        """The smallest box that covers both this one and `other`."""
        return Rect(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass(frozen=True, slots=True)
class Page:
    """A page's size in points, unrotated like every box here, and its turn.

    `rotation` is the file's own /Rotate, clockwise: 0, 90, 180 or 270. Nothing
    on the server applies it; the browser turns the page.
    """

    width: float
    height: float
    rotation: int


@dataclass(frozen=True, slots=True)
class Fragment:
    """One piece of text the file draws in one go.

    PDF writers split a sentence into many of these to adjust letter spacing, so
    a fragment is often a few letters and sometimes half a word. Users never see
    fragments; they exist so a merged span can be redrawn accurately.
    """

    text: str
    bbox: Rect
    origin: tuple[float, float]


@dataclass(frozen=True, slots=True)
class Span:
    """A run of text the user can edit, merged from one or more fragments.

    `font` is the name as the file records it, subset prefix and all
    (`ABCDEE+Calibri`). `origin` is the baseline start, not the top-left of the
    box: two points out is visible.

    `id` is stable for the life of a document because the index is built once
    from the pristine file and never rebuilt from an edited one. See SpanIndex.
    """

    id: str
    page: int
    text: str
    font: str
    size: float
    color: tuple[float, float, float]
    bbox: Rect
    origin: tuple[float, float]
    fragments: tuple[Fragment, ...]

    @property
    def merged(self) -> bool:
        """True when this span is stitched from more than one fragment."""
        return len(self.fragments) > 1


@dataclass(frozen=True, slots=True)
class FontCode:
    """One code in a font, and what it draws."""

    value: int  # the code the page writes, e.g. 0x21
    letter: str  # the letter the font's letter list (ToUnicode) says it is
    glyph: GlyphId  # the shape it draws; 0 means none
    width: float  # per 1000 em, from the font's width list


@dataclass(frozen=True, slots=True)
class CodedFont:
    """An embedded font the page writes with codes, not letters.

    The font can't look letters up itself; its letter list (ToUnicode) says which
    code is which letter.
    """

    resource: str  # its name in the page's font resources, e.g. "F1"
    xref: int  # its PDF object
    code_bytes: int  # bytes per code: 1 for a simple font, 2 for Type0
    letters: dict[str, FontCode]  # each letter it can write, and the code for it


type Style = Literal["regular", "bold", "italic", "bold-italic"]
type Category = Literal["sans", "serif", "mono", "handwriting"]


@dataclass(frozen=True, slots=True)
class Face:
    """A font file we ship: one family in one style."""

    name: str  # what the user reads and an insert asks for, e.g. "Carlito Bold"
    family: str  # e.g. "Carlito"
    style: Style
    category: Category
    file: str  # its file in the package's `fonts` folder
    license: str  # e.g. "OFL-1.1"; the text is in `fonts/licenses`
    same_widths_as: tuple[str, ...]  # document fonts whose letters are exactly as wide


@dataclass(frozen=True, slots=True)
class LookAlike:
    """The face that stands in for a document's font, and whether nothing on the page moves."""

    face: Face
    same_widths: bool  # False when it's only the same kind of font (a serif for a serif)


@dataclass(frozen=True, slots=True)
class FontDescriptor:
    """What a font's description in the PDF says about its look, for picking a look-alike."""

    flags: int  # its /Flags bits: serif, fixed width, italic, forced bold and so on
    weight: float | None  # its /FontWeight, 100 to 900, when it gives one
    italic_angle: float  # its /ItalicAngle: how far the letters lean, 0 when upright


def new_text(
    page: int,
    origin: tuple[float, float],
    text: str,
    size: float,
    font: str,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Span:
    """A span for text that isn't in the document yet, so it's judged and drawn like any other.

    Its box is only nominal: nothing reads it for new text.
    """
    x, y = origin
    box = Rect(x, y - size, x, y)
    return Span("", page, text, font, size, color, box, origin, ())


class SpanIndex:
    """Every editable span in a document, built once and never rebuilt.

    This is the invariant that keeps span ids stable: the index always describes
    the *original* file. Edits are applied on top when rendering, but the index
    itself is never re-extracted from a patched document. Rebuild it and every id
    changes, and every reference the client holds dangles.
    """

    def __init__(self, spans: list[Span]) -> None:
        """Build the index once from the full list of spans in a document."""
        self._by_id = {s.id: s for s in spans}
        self._order = tuple(spans)

    def __iter__(self) -> Iterator[Span]:
        """Every span, in the order it was extracted."""
        return iter(self._order)

    def __len__(self) -> int:
        """How many spans are in the document."""
        return len(self._order)

    def get(self, span_id: str) -> Span | None:
        """Look up one span by id, or None if it does not exist."""
        return self._by_id.get(span_id)


# 6 bytes -> 12 hex chars: documents have thousands of spans at most, nowhere
# near enough for a collision at this length.
_SPAN_ID_DIGEST_SIZE = 6


def span_id(page: int, bbox: Rect, font: str, text: str, ordinal: int) -> str:
    """A span's id: stable within a document, distinct between near-identical cells.

    The ordinal separates spans that share text, font and a rounded box (two
    empty table cells, say), which a content hash alone would collide.
    """
    seed = f"{page}:{bbox.x0:.1f}:{bbox.y0:.1f}:{font}:{text}:{ordinal}"
    return hashlib.blake2s(seed.encode(), digest_size=_SPAN_ID_DIGEST_SIZE).hexdigest()
