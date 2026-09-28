"""A redaction's life, from the edit list to the saved file.

Rule 4: a redaction is checked by re-reading what was saved. This follows one
edit list's redactions all the way: which spans they are, worked out before
anything is drawn; whether their text is gone from the document in memory;
and whether it is gone from the file as saved. The CLI's `redact` goes through
it, and so does every download.
"""

from __future__ import annotations

from collections.abc import Sequence

from squidpdf.core import Engine, Span, SpanIndex, open_pdf
from squidpdf.editing.apply import resolve
from squidpdf.editing.edits import Edit, Redact
from squidpdf.editing.errors import RedactionFailed


class RedactionController:
    """One edit list's redactions, followed from the edit list to the saved file."""

    def __init__(self, redacted: Sequence[Span]) -> None:
        """Follow these redacted spans, as the original has them."""
        self._redacted = list(redacted)

    @classmethod
    def from_edits(
        cls, engine: Engine, edits: Sequence[Edit], index: SpanIndex
    ) -> RedactionController:
        """The redactions an edit list leaves: every span whose last edit is a Redact.

        Worked out before anything is drawn. A Replace after a Redact means it
        was not redacted after all. A redaction pointing at text the index
        doesn't have raises BadReference, as apply() does: skipping it would
        be a leak.
        """
        span_edits, _inserts, _skipped = resolve(engine, edits, index)
        return cls([span for edit, span in span_edits if isinstance(edit, Redact)])

    def verdicts(self, engine: Engine) -> dict[str, bool]:
        """Whether each redacted span's text is gone from the document in memory, by span id.

        Render's early verdict, before anything is saved. A covering rectangle
        would pass a visual check and fail this one. The same words elsewhere,
        such as a header repeated on other pages, don't count against it.
        """
        return {span.id: engine.absent(span) for span in self._redacted}

    def check_saved(self, path: str) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Read from the saved bytes, so a covering box or a save that lost the
        removal can't pass. Raises RedactionFailed, naming the first span
        whose text is still there.
        """
        with open_pdf(path) as saved:
            for span in self._redacted:
                if not saved.absent(span):
                    raise RedactionFailed(span.id, span.text, span.page + 1)
