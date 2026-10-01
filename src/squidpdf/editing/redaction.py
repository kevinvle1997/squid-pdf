"""A redaction's life: which spans it covers, then whether their text is really gone.

Checked by re-reading the saved file. Export and the CLI's `redact` both use it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence

from squidpdf.core import Engine, Span, open_pdf
from squidpdf.editing.errors import RedactionFailed

__all__ = [
    "RedactionController",
]


class RedactionController:
    """One edit list's redactions, from the edits to the saved file."""

    def __init__(self, redacted: Sequence[Span]) -> None:
        """Follow these redacted spans, numbered as in the original."""
        self._redacted = list(redacted)

    def verdicts(self, engine: Engine) -> dict[str, bool]:
        """Whether each redacted span's text is gone from the document in memory, by span id.

        Render's early check, before anything is saved, on pages still numbered
        as in the original.
        """
        left = {span.id for span in engine.still_there(self._redacted)}
        return {span.id: span.id not in left for span in self._redacted}

    def check_saved(self, path: str, *, pages: Sequence[int]) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        `pages` is the saved file's page order, by original number, as `page_order`
        worked it out: each span is read on the page it went to. Raises
        RedactionFailed naming the first span still there, by its original page.
        """
        with open_pdf(path) as saved:
            left = saved.still_there(as_saved(self._redacted, pages))
        if left:
            first = next(span for span in self._redacted if span.id == left[0].id)
            raise RedactionFailed(first.id, first.text, first.page + 1)


def as_saved(redacted: Sequence[Span], pages: Sequence[int]) -> Iterator[Span]:
    """Each redacted span on the page it went to in `pages`; none on a page left out."""
    by_page: dict[int, list[Span]] = {}
    for span in redacted:
        by_page.setdefault(span.page, []).append(span)
    for saved_page, original_page in enumerate(pages):
        # .get: most pages have no redaction on them.
        for span in by_page.get(original_page, []):
            yield dataclasses.replace(span, page=saved_page)
