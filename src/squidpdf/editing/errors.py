from __future__ import annotations

from squidpdf.core import Problem


class BadReference(Problem):
    type = "bad_reference"
    status = 422

    def __init__(self, span_id: str) -> None:
        super().__init__(span_id=span_id)

    @property
    def span_id(self) -> str:
        return str(self.fill["span_id"])


class BadRange(Problem):
    type = "bad_range"
    status = 422

    def __init__(self, span_id: str, *, debug: str | None = None) -> None:
        super().__init__(debug, span_id=span_id)


class RedactionFailed(Problem):
    type = "redaction_failed"
    status = 422

    def __init__(self, span_id: str, text: str, page: int) -> None:
        super().__init__(span_id=span_id, text=text, page=page)


class RedactionMoved(Problem):
    type = "redaction_moved"
    status = 422

    def __init__(self, text: str, page: int) -> None:
        super().__init__(text=text, page=page)


class RedactionUnchecked(Problem):
    type = "redaction_unchecked"
    status = 422

    def __init__(self, text: str, page: int) -> None:
        super().__init__(text=text, page=page)


class RedactionCovered(Problem):
    type = "redaction_covered"
    status = 422

    def __init__(self, span_id: str, page: int) -> None:
        super().__init__(span_id=span_id, page=page)


class TooManyEdits(Problem):
    type = "too_many_edits"
    status = 422

    def __init__(self, edits: int) -> None:
        super().__init__(edits=edits)


class TextTooLong(Problem):
    type = "text_too_long"
    status = 422

    def __init__(self, chars: int) -> None:
        super().__init__(chars=chars)


class RedactionConflict(Problem):
    type = "redaction_conflict"
    status = 422
