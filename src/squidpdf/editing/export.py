"""Export, end to end: the edits applied to the whole document, and the file handed back.

ExportController checks the request, sends the work to the workers and hands
back the file, the edits it left out and what making it did other than asked,
in no one's words yet: `editing/api.py` puts them into the reader's. Nothing
here imports the web framework, so a worker can import it to run the work.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from squidpdf.core import InvalidRequest, Message, Workers, open_pdf
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.editing.apply import apply
from squidpdf.editing.constants import EXPORT_TIMEOUT_S, MAX_EDITS, MAX_TEXT_CHARS
from squidpdf.editing.edits import Edit, Insert, Replace
from squidpdf.editing.errors import TextTooLong, TooManyEdits
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import Exported

__all__ = [
    "ExportController",
]

_EXPORTED = "export.pdf"  # the file saved, then checked, before its bytes go back


class ExportController:
    """Export, from the request to the checked file, over the app's workers."""

    def __init__(self, workers: Workers) -> None:
        """Do the work on `workers`, never on the server's own thread."""
        self._workers = workers

    async def export(
        self, folder: Path, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        """The document with the edits applied, as a PDF: every page, or `pages` in that order.

        `pages` are original numbers. Refuses an edit list over the limits and a
        page list the document can't give. Each redaction is checked on the
        saved file: one still there fails the whole request, and no file comes
        back. Keeps nothing.
        """
        if len(edits) > MAX_EDITS:
            raise TooManyEdits(MAX_EDITS)
        too_long = any(
            isinstance(edit, Replace | Insert) and len(edit.text) > MAX_TEXT_CHARS
            for edit in edits
        )
        if too_long:
            raise TextTooLong(MAX_TEXT_CHARS)
        if pages is not None:
            check_pages(pages, len(store.load_pages(folder)))
        # A redaction pointing at nothing, or not gone from the file, raises in the worker.
        return await self._workers.run(
            EXPORT_TIMEOUT_S, ExportController.work, str(folder), edits, pages
        )

    @staticmethod
    def work(folder: str, edits: list[Edit], pages: list[int] | None) -> Exported:
        """The workers' part: every edit applied, the pages kept, saved, the redactions checked.

        A staticmethod, so a worker can import it by name. Render applies only
        the edits on the pages it draws; this applies them all. The index is
        the saved one: rebuilt, every id would change.
        """
        path = Path(folder)
        index = store.load_index(path)
        if index is None:  # upload saves it before it answers, so only a sweep removes it
            raise Gone

        # In the document's folder, so a killed worker's file is swept with the document.
        with tempfile.TemporaryDirectory(dir=path) as scratch:
            saved = str(Path(scratch) / _EXPORTED)
            with open_pdf(str(path / store.ORIGINAL)) as engine:
                redactions = RedactionController.from_edits(engine, edits, index)
                applied = apply(engine, edits, index)
                said: list[Message] = []
                if pages is not None:
                    said += redactions.keep_pages(engine, pages)
                said += engine.save(saved)
            redactions.check_saved(saved)
            skipped = [skip.edit for skip in applied.skipped]
            # Only the file's own: apply's are render's, said there for the same edits.
            return Exported(Path(saved).read_bytes(), skipped, said)


def check_pages(pages: list[int], page_count: int) -> None:
    """Refuse a page list export can't give: none, one the document lacks, or one twice."""
    if not pages:
        raise InvalidRequest(debug="pages: empty; leave it out for every page")
    outside = [page for page in pages if not 0 <= page < page_count]
    if outside:
        raise NoSuchPage(debug=f"pages: no page {outside[0]}")
    if len(set(pages)) != len(pages):
        raise InvalidRequest(debug="pages: each page at most once")
