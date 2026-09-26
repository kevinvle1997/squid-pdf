"""What can go wrong with the edits a browser sends."""

from __future__ import annotations

from squidpdf.core import Problem, words


class BadReference(Problem):
    """A redaction points at text that isn't there. Skipping it would be a leak."""

    type = "bad_reference"
    status = 422
    sentence = words.BAD_REFERENCE

    def __init__(self, span_id: str) -> None:
        """Name the span the redaction asked for."""
        super().__init__(span_id=span_id)

    @property
    def span_id(self) -> str:
        """The span the redaction asked for."""
        return str(self.fill["span_id"])


class TooManyEdits(Problem):
    """More edits in one request than the server applies."""

    type = "too_many_edits"
    status = 422
    sentence = words.TOO_MANY_EDITS

    def __init__(self, edits: int) -> None:
        """Name the limit it went over."""
        super().__init__(edits=edits)


class TextTooLong(Problem):
    """A replacement over the length limit."""

    type = "text_too_long"
    status = 422
    sentence = words.TEXT_TOO_LONG

    def __init__(self, chars: int) -> None:
        """Name the limit it went over."""
        super().__init__(chars=chars)


# Not raised yet: kept for the browser, which already branches on them.
class RedactionConflict(Problem):
    """Editing text that has a redaction on it."""

    type = "redaction_conflict"
    status = 422
    sentence = words.REDACTION_CONFLICT


class FontMismatch(Problem):
    """A font that isn't the one the document uses."""

    type = "font_mismatch"
    status = 422
    sentence = words.FONT_MISMATCH
