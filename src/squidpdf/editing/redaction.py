"""A redaction's life: which spans it covers, then whether their text is really gone.

Checked by re-reading the saved file. Export and the CLI's `redact` both use it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence

from squidpdf.core import Engine, Message, Span, SpanIndex, open_pdf
from squidpdf.editing.apply import resolve
from squidpdf.editing.edits import Edit, Redact
from squidpdf.editing.errors import RedactionFailed


class RedactionController:
    """One edit list's redactions, from the edits to the saved file."""

    def __init__(self, redacted: Sequence[Span]) -> None:
        """Follow these redacted spans, numbered as in the original."""
        self._redacted = list(redacted)
        # The pages kept, in their new order; None when every page stays put.
        self._kept: list[int] | None = None

    @classmethod
    def from_edits(
        cls, engine: Engine, edits: Sequence[Edit], index: SpanIndex
    ) -> RedactionController:
        """Every span whose last edit is a Redact, worked out before anything is drawn.

        A Replace after a Redact undoes it. A redaction pointing at nothing
        raises BadReference: skipping it would leak.
        """
        span_edits, _inserts, _skipped = resolve(engine, edits, index)
        return cls([span for edit, span in span_edits if isinstance(edit, Redact)])

    def verdicts(self, engine: Engine) -> dict[str, bool]:
        """Whether each redacted span's text is gone from the document in memory, by span id.

        Render's early check, before anything is saved.
        """
        left = {span.id for span in engine.still_there(self._as_saved())}
        return {span.id: span.id not in left for span in self._redacted}

    def keep_pages(self, engine: Engine, pages: Sequence[int]) -> list[Message]:
        """Keep only `pages`, in that order. Returns what keeping them said.

        Here, not on the engine, so each check reads a span on the page it moved to.
        """
        self._kept = list(pages)
        return engine.keep_pages(self._kept)

    def check_saved(self, path: str) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Raises RedactionFailed naming the first span still there, by its original page.
        """
        with open_pdf(path) as saved:
            left = saved.still_there(self._as_saved())
        if left:
            first = next(span for span in self._redacted if span.id == left[0].id)
            raise RedactionFailed(first.id, first.text, first.page + 1)

    def _as_saved(self) -> Iterator[Span]:
        """Each redacted span on the page it went to; none on a page left out."""
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
