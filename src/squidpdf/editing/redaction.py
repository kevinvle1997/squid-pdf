"""A redaction's life, from the edit list to the saved file.

Rule 4: a redaction is checked by re-reading what was saved. This follows one
edit list's redactions all the way: which spans they are, worked out before
anything is drawn; whether their text is gone from the document in memory;
and whether it is gone from the file as saved. The CLI's `redact` goes through
it, and so does every download.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence

from squidpdf.core import Engine, Span, SpanIndex, open_pdf
from squidpdf.editing.apply import resolve
from squidpdf.editing.edits import Edit, Redact
from squidpdf.editing.errors import RedactionFailed


class RedactionController:
    """One edit list's redactions, followed from the edit list to the saved file."""

    def __init__(self, redacted: Sequence[Span]) -> None:
        """Follow these redacted spans, as the original has them."""
        self._redacted = list(redacted)
        # The saved file's pages as original numbers, in its order; None when all stay put.
        self._kept: list[int] | None = None

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
        left = {span.id for span in engine.still_there(self._redacted)}
        return {span.id: span.id not in left for span in self._redacted}

    def pages_kept(self, pages: Sequence[int]) -> None:
        """The saved file keeps only `pages`, original numbers in its order.

        Each redaction moves with its page; one on a page left out goes with it.
        """
        self._kept = list(pages)

    def check_saved(self, path: str) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Read from the saved bytes, so a covering box or a save that lost the
        removal can't pass. Each span is read in its own box, on the page it
        went to, and each page once. Raises RedactionFailed, naming a span
        whose text is still there by its original page.
        """
        with open_pdf(path) as saved:
            left = saved.still_there(self._as_saved())
        if left:
            first = next(span for span in self._redacted if span.id == left[0].id)
            raise RedactionFailed(first.id, first.text, first.page + 1)

    def _as_saved(self) -> Iterator[Span]:
        """Each redacted span on the page it went to in the saved file; none left out."""
        # Every page stayed where it was.
        if self._kept is None:
            yield from self._redacted
            return
        by_page: dict[int, list[Span]] = {}
        for span in self._redacted:
            by_page.setdefault(span.page, []).append(span)
        for place, original in enumerate(self._kept):
            # .get: most pages have no redaction on them.
            for span in by_page.get(original, []):
                yield dataclasses.replace(span, page=place)
