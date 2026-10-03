"""Everything the user did, as one ordered list: the browser keeps it and sends it whole.

One union rather than a list per kind, so undo in the browser is truncation
whatever was undone. The server applies the list as its final state: each span
once, as its last edit leaves it, except that a redaction is never undone by
an edit after it (the whole list is refused).
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Literal, assert_never

from squidpdf.editing import constants
from squidpdf.editing.errors import TextTooLong, TooManyEdits

# How a too-long replacement is drawn; the names the user's options go by.
type Strategy = Literal["as-is", "shrink", "condense"]

# Unicode's categories not on a line: controls, line and paragraph separators, half an emoji.
# typing.ts's NOT_ON_A_LINE copies this and _BIDI_CONTROLS: change both.
_NOT_ON_A_LINE = frozenset({"Cc", "Zl", "Zp", "Cs"})

# Bidi embeds, overrides and isolates, which reorder the line; not joiners, which are text.
_BIDI_CONTROLS = (range(0x202A, 0x202F), range(0x2066, 0x206A))


def check_text(text: str) -> None:
    """Refuse new text that isn't one line of letters, however the edit is made.

    A line break would draw a second line over the next (reflow is out of
    scope), and the rest draw nothing. From the browser, pydantic turns the
    ValueError into a bad request.
    """
    for position, ch in enumerate(text):
        if not _is_on_a_line(ch):
            raise ValueError(f"text: U+{ord(ch):04X} at {position}; new text is one line")


def _is_on_a_line(ch: str) -> bool:
    """Whether a character can stand in one line of new text."""
    bidi_control = any(ord(ch) in controls for controls in _BIDI_CONTROLS)
    return not bidi_control and unicodedata.category(ch) not in _NOT_ON_A_LINE


@dataclass(frozen=True, slots=True)
class Replace:
    """Swap a span's text: remove the old letters and draw the new ones in their place."""

    span_id: str
    text: str
    strategy: Strategy = "as-is"
    kind: Literal["replace"] = "replace"

    def __post_init__(self) -> None:
        """Refuse text that isn't one line of letters."""
        check_text(self.text)


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

    `font` names a face we ship (`core.fonts.catalog.FACES`, e.g. "Caveat Bold") or a
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
        """Refuse a place, size, color or text nothing can draw, however the insert is made.

        From the browser, pydantic turns the ValueError into a bad request.
        """
        if not all(math.isfinite(xy) for xy in self.origin):
            raise ValueError(f"origin must be finite, got {self.origin}")
        if not (math.isfinite(self.size) and self.size > 0):
            raise ValueError(f"size must be above 0, got {self.size}")
        if not all(0 <= channel <= 1 for channel in self.color):
            raise ValueError(f"color channels must be from 0 to 1, got {self.color}")
        check_text(self.text)


# Plain aliases, not `type` statements: pydantic gives those a schema, changing the wire's.
# An edit to text the document has, named by its span.
SpanEdit = Replace | Redact
# An edit to a page as a whole, named by its page.
PageEdit = Insert
# Every edit the browser can send. Each place that treats kinds differently ends in
# `assert_never`, so a new kind is a type error until every one handles it.
Edit = SpanEdit | PageEdit


def check_edits(edits: list[Edit]) -> None:
    """Refuse an edit list over the limits: too many edits, or too much text in one."""
    # Read as module attributes, so a test can lower the limits.
    if len(edits) > constants.MAX_EDITS:
        raise TooManyEdits(constants.MAX_EDITS)
    longest = constants.MAX_TEXT_CHARS
    too_long = any(len(_typed_in(edit)) > longest for edit in edits)
    if too_long:
        raise TextTooLong(longest)


def _typed_in(edit: Edit) -> str:
    """The new text an edit carries: empty for a redaction, which draws none."""
    # A replace or an insert: the text it draws.
    if isinstance(edit, Replace | Insert):
        return edit.text
    # A redaction: it draws none.
    if isinstance(edit, Redact):
        return ""
    assert_never(edit)
