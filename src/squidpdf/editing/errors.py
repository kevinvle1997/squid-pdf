"""What can go wrong with the edits a browser sends."""

from __future__ import annotations

from squidpdf.core import Problem


class BadReference(Problem):
    """A redaction points at text that isn't there. Skipping it would be a leak."""

    type = "bad_reference"
    status = 422

    def __init__(self, span_id: str) -> None:
        """Name the span the redaction asked for."""
        super().__init__(span_id=span_id)

    @property
    def span_id(self) -> str:
        """The span the redaction asked for."""
        return str(self.fill["span_id"])


class RedactionFailed(Problem):
    """A redacted span's text is still in the saved file, so no file is sent."""

    type = "redaction_failed"
    status = 422

    def __init__(self, span_id: str, text: str, page: int) -> None:
        """Name the span, its text, and its page counted from 1."""
        super().__init__(span_id=span_id, text=text, page=page)


class TooManyEdits(Problem):
    """More edits in one request than the server applies."""

    type = "too_many_edits"
    status = 422

    def __init__(self, edits: int) -> None:
        """Name the limit it went over."""
        super().__init__(edits=edits)


class TextTooLong(Problem):
    """New text over the length limit: a replacement's or an insert's."""

    type = "text_too_long"
    status = 422

    def __init__(self, chars: int) -> None:
        """Name the limit it went over."""
        super().__init__(chars=chars)


class RedactionConflict(Problem):
    """Editing text redacted earlier in the list: redaction wins, so the whole list fails."""

    type = "redaction_conflict"
    status = 422
