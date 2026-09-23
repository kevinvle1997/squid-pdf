"""What the PDF says. Facts only.

A Span holds what the file states and nothing we concluded. Whether an edit here
will look identical depends on the font library we happen to ship, which is a
judgement that can change without the document changing — so it lives in
`core.fidelity`, not here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass


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
class Fragment:
    """One show-text operator as the file records it.

    Generators split a sentence into several of these so they can insert kerning,
    so a fragment is often a few letters and sometimes half a word. Users never
    see fragments; they exist so a merged span can be redrawn accurately.
    """

    text: str
    bbox: Rect
    origin: tuple[float, float]


@dataclass(frozen=True, slots=True)
class Span:
    """A run of text the user can edit, merged from one or more fragments.

    `font` is the name as the file records it, subset prefix and all
    (`ABCDEE+Calibri`). `origin` is the baseline start, not the top-left of the
    box — two points out is visible.

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


def span_id(page: int, bbox: Rect, font: str, text: str, ordinal: int) -> str:
    """Stable within a document, distinct between near-identical cells.

    The ordinal separates spans that share text, font and a rounded box — two
    empty table cells, say — which a content hash alone would collide.
    """
    seed = f"{page}:{bbox.x0:.1f}:{bbox.y0:.1f}:{font}:{text}:{ordinal}"
    return hashlib.blake2s(seed.encode(), digest_size=6).hexdigest()
