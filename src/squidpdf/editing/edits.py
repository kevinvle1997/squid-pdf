"""Everything the user did, as one ordered list: the browser keeps it and sends it whole.

One union, not a list per kind, so undo in the browser is truncation. The server applies its
final state, each span once as its last edit leaves it; an edit that would undo a redaction
refuses the whole list.
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
    # Each character refused below is unprintable, so printable text passes as it is.
    if text.isprintable():
        return
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
class RunText:
    """One span of a line, or part of it, and the words it's to read, in the span's own style.

    `start` and `end` are where in the span's text the edit begins and ends, in code points,
    where a split cuts the span; `text` is the words of that part alone.
    """

    span_id: str
    text: str
    start: int | None = None
    end: int | None = None

    def __post_init__(self) -> None:
        """Refuse text that isn't one line of letters, or a part no text could have."""
        check_text(self.text)
        before_the_text = self.start is not None and self.start < 0
        if before_the_text:
            raise ValueError(f"start: {self.start} is before the span's text")
        start = self.start or 0
        if self.end is not None and self.end <= start:
            raise ValueError(f"end: {self.end} is not after start {start}")


@dataclass(frozen=True, slots=True)
class ReplaceRuns:
    """Swap a line's words, run by run: each run's words drawn in its own span's style.

    The runs are consecutive spans of one line (`Document.lines`), or a span on none. Those
    after a change move along, unless a way out holds them; text past a split stays put.
    """

    runs: tuple[RunText, ...]
    strategy: Strategy = "as-is"
    kind: Literal["replace_runs"] = "replace_runs"

    def __post_init__(self) -> None:
        """Refuse a line emptied whole, and a split anywhere but at the edit's two ends."""
        if not any(run.text for run in self.runs):
            raise ValueError("runs: every one is empty; taking text out is a redaction's job")
        starts_inside = any(run.start is not None for run in self.runs[1:])
        ends_inside = any(run.end is not None for run in self.runs[:-1])
        if starts_inside or ends_inside:
            raise ValueError("runs: only the first may have a start, and only the last an end")


@dataclass(frozen=True, slots=True)
class Redact:
    """Remove a span's text, or the words `range` names, and draw nothing in their place.

    A covering box would leave the text extractable underneath, so this deletes it from the
    page itself, and the output is re-read to check.
    """

    span_id: str
    # The words' start and end in the span's text, in code points; None takes all of it.
    range: tuple[int, int] | None = None
    kind: Literal["redact"] = "redact"


@dataclass(frozen=True, slots=True)
class Insert:
    """Draw new text where the document has none: a signature, an annotation.

    `font` names a face we ship (`core.fonts.catalog.FACES`, e.g. "Caveat Bold") or a font
    this page uses, by `font_object` too where given: two can read by one name. Anything
    else is drawn in a look-alike, and its fit says so: the fit says what will really draw.
    """

    page: int
    origin: tuple[float, float]
    text: str
    size: float
    font: str = "Liberation Sans Regular"
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    font_object: int | None = None
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


@dataclass(frozen=True, slots=True)
class RotatePage:
    """Turn a page clockwise by `degrees`, on top of the turn the file gives it."""

    page: int
    degrees: Literal[90, 180, 270]
    kind: Literal["rotate_page"] = "rotate_page"


@dataclass(frozen=True, slots=True)
class DeletePage:
    """Leave a page out of the file, and everything on it."""

    page: int
    kind: Literal["delete_page"] = "delete_page"


@dataclass(frozen=True, slots=True)
class MovePage:
    """Put a page just before page `before`, or after the last page when it's None."""

    page: int
    before: int | None
    kind: Literal["move_page"] = "move_page"


# Plain aliases, not `type` statements: pydantic gives those a schema, changing the wire's.
# An edit to text the document has, named by its span.
SpanEdit = Replace | Redact
# An edit to a line the document has, named by its spans.
LineEdit = ReplaceRuns
# An edit to a page's place, turn or presence in the file, named by its original number.
PageOp = RotatePage | DeletePage | MovePage
# An edit to a page as a whole, named by its page.
PageEdit = Insert | PageOp
# Every edit the browser can send. Each place that treats kinds differently ends in
# `assert_never`, so a new kind is a type error until every one handles it.
Edit = SpanEdit | LineEdit | PageEdit


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
    """The new text an edit carries, a line's runs together; empty for one that draws none."""
    if isinstance(edit, Replace | Insert):
        return edit.text
    if isinstance(edit, ReplaceRuns):
        return "".join(run.text for run in edit.runs)
    if isinstance(edit, Redact | PageOp):
        return ""
    assert_never(edit)
