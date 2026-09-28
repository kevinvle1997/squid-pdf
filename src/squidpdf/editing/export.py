"""Export: the edits applied to the whole document, and the file sent back.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from squidpdf.core import InvalidRequest, Message, Workers, open_pdf, words
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply
from squidpdf.editing.constants import EXPORT_TIMEOUT_S, MAX_EDITS, MAX_TEXT_CHARS
from squidpdf.editing.edits import Edit, Insert, Replace
from squidpdf.editing.errors import TextTooLong, TooManyEdits
from squidpdf.editing.info import notice_info
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import Exported, ExportReply, Notice

__all__ = [
    "ExportController",
]

_EXPORTED = "export.pdf"  # the file saved, then checked, before its bytes go back

# The body is the file, so these headers carry the rest.
_SKIPPED_HEADER = "Squid-Skipped-Edits"
_NOTICES_HEADER = "Squid-Notices"


class ExportController:
    """Export, from request to reply."""

    def __init__(self, workers: Workers) -> None:
        """Make files on `workers`, off the server's own thread."""
        self._workers = workers

    async def export(
        self, doc: Loaded, *, edits: list[Edit], pages: list[int] | None, said_in: str
    ) -> ExportReply:
        """The edited PDF, every page or `pages` in that order, and headers for the rest.

        Refuses edits over the limits and pages the document lacks. A
        redaction still in the saved file fails the whole request.
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
            check_pages(pages, len(store.load_pages(doc.folder)))
        exported = await self._workers.run(
            EXPORT_TIMEOUT_S,
            ExportController.make_pdf,
            folder=str(doc.folder),
            edits=edits,
            pages=pages,
        )
        said = [notice_info(Notice(None, message), said_in) for message in exported.notices]
        headers = {
            _SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped),
            # ASCII only, so a header in any language stays valid.
            _NOTICES_HEADER: json.dumps(said, ensure_ascii=True),
            **words.language_headers(said_in),
        }
        return ExportReply(exported.pdf, headers)

    @staticmethod
    def make_pdf(folder: str, *, edits: list[Edit], pages: list[int] | None) -> Exported:
        """Apply every edit, keep the pages, save, and check the redactions in the saved file.

        Runs in a worker, so it's a staticmethod the worker can import by name.
        """
        path = Path(folder)
        index = store.load_index(path)
        if index is None:  # only a sweep removes it
            raise Gone

        # In the document's folder, so a killed worker's file is swept with it.
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
            # Only the file's own notices: render already said apply's.
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
