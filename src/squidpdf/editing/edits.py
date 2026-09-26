"""Everything the user did, as one ordered log.

One union rather than a list per kind. Undo is then truncation regardless of what
was undone, and export applies a single list in order, which matters when a
redaction and an edit touch the same region and the result depends on which
happened first.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

from squidpdf.editing.types import Strategy


@dataclass(frozen=True, slots=True)
class Replace:
    """Swap a span's text: remove the old letters and draw the new ones in their place."""

    span_id: str
    text: str
    strategy: Strategy = "as-is"
    kind: Literal["replace"] = "replace"


@dataclass(frozen=True, slots=True)
class Redact:
    """Remove a span's text and draw nothing in its place.

    Mechanically the first half of a Replace. A covering rectangle would leave
    the text extractable underneath, which is how documents get leaked, so this
    deletes the text from the page itself and the result is checked by re-reading
    the output.
    """

    span_id: str
    kind: Literal["redact"] = "redact"


@dataclass(frozen=True, slots=True)
class Insert:
    """Draw new text where the document has none: a signature, an annotation.

    `font` names a face we ship (`core.fonts.FACES`, e.g. "Caveat Bold") or a
    font the document uses on this page. Anything else is drawn in a look-alike,
    and its fit says so: the fit always says what will really be drawn.
    """

    page: int
    origin: tuple[float, float]
    text: str
    size: float
    font: str = "Liberation Sans Regular"
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    kind: Literal["insert"] = "insert"

    def __post_init__(self) -> None:
        """Refuse a place, size or color nothing can draw, however the insert is made.

        From the browser, pydantic turns the ValueError into a bad request.
        """
        if not all(math.isfinite(xy) for xy in self.origin):
            raise ValueError(f"origin must be finite, got {self.origin}")
        if not (math.isfinite(self.size) and self.size > 0):
            raise ValueError(f"size must be above 0, got {self.size}")
        if not all(0 <= channel <= 1 for channel in self.color):
            raise ValueError(f"color channels must be from 0 to 1, got {self.color}")


type Edit = Replace | Redact | Insert


class EditLog:
    """The ordered list of edits against one document.

    Held by the client in the stateless design and sent with each render, so this
    is a value object: no document, no engine, nothing that cannot be serialised.
    `edits` is a plain list: append, iterate, or take its length directly.
    """

    def __init__(self, edits: list[Edit] | None = None) -> None:
        """Start a log, optionally pre-loaded with edits already made."""
        self.edits: list[Edit] = list(edits or [])

    def undo(self) -> Edit | None:
        """Truncation. Works the same for every kind, which is the point."""
        return self.edits.pop() if self.edits else None

    def touching(self, span_id: str) -> list[Edit]:
        """Every edit in the log that touched this span, in order."""
        return [e for e in self.edits if getattr(e, "span_id", None) == span_id]

    def as_list(self) -> list[dict]:
        """The log as plain dicts, for sending over the wire."""
        return [asdict(e) for e in self.edits]
