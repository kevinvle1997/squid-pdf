"""A redaction's life: which spans it covers, then whether their text is really gone.

Checked by re-reading the saved file. Export and the CLI's `redact` both use it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from squidpdf.core import Engine, HiddenPlace, Span, open_pdf
from squidpdf.editing.errors import RedactionFailed


@dataclass(frozen=True, slots=True, eq=False)
class RedactionController:
    """One edit list's redactions, from the edits to the saved file."""

    redacted: tuple[Span, ...]  # the spans it follows, numbered as in the original

    def hidden_places(self, engine: Engine) -> dict[str, list[HiddenPlace]]:
        """Where else each redacted span's words have a hidden copy, by span id.

        Render's word on them, read from the document in memory before the
        edits run: running them takes the words out of each.
        """
        return engine.hidden_places(self.redacted)

    def verdicts(self, engine: Engine) -> dict[str, bool]:
        """Whether each redacted span's words are gone from the document in memory, by span id.

        Render's early run of the check export makes, before anything is saved,
        on pages still numbered as in the original.
        """
        redacted = list(self.redacted)
        left = {span.id for span in _words_left(engine, redacted, redacted=redacted)}
        return {span.id: span.id not in left for span in self.redacted}

    def check_saved(self, path: str, *, pages: Sequence[int]) -> None:
        """Re-open the file saved at `path` and confirm each redacted span's text is gone.

        Gone from its box, from the hidden copies on its page, and from the
        document's own: its title, metadata, bookmarks, comments, form fields
        and tags. `pages` is the saved file's page order, by original number,
        as `page_order` worked it out: each span is read on the page it went
        to, and in the document's own even when its page was left out. Raises
        RedactionFailed naming the first span still there, by its original page.
        """
        as_saved = list(_as_saved(self.redacted, pages))
        with open_pdf(path) as saved:
            left = _words_left(saved, as_saved, redacted=self.redacted)
        if left:
            first = next(span for span in self.redacted if span.id == left[0].id)
            raise RedactionFailed(first.id, first.text, first.page + 1)


def _words_left(engine: Engine, placed: list[Span], *, redacted: Sequence[Span]) -> list[Span]:
    """The spans with words still in the file: in their box, or a hidden copy anywhere.

    The one check, which render runs early and export on the saved file, so
    the warning and the refusal can't drift apart. `placed` are the spans on
    the pages they're read on, for their boxes and their pages' hidden
    copies; `redacted` are all of them, their pages kept or not, for the
    document's own, none of which sits on one page.
    """
    on_pages = engine.still_there(placed) + engine.still_hidden(placed)
    return on_pages + engine.still_hidden_in_document(redacted)


def _as_saved(redacted: Sequence[Span], pages: Sequence[int]) -> Iterator[Span]:
    """Each redacted span on the page it went to in `pages`; none on a page left out."""
    by_page: dict[int, list[Span]] = {}
    for span in redacted:
        by_page.setdefault(span.page, []).append(span)
    for saved_page, original_page in enumerate(pages):
        # .get: most pages have no redaction on them.
        for span in by_page.get(original_page, []):
            yield replace(span, page=saved_page)
