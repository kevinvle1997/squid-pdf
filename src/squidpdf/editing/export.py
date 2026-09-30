"""Export: the edits applied to the whole document, and the file sent back.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import json
import tempfile
from functools import partial
from pathlib import Path

from squidpdf.core import Engine, InvalidRequest, Reply, SpanIndex, Workers, open_pdf, words
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply
from squidpdf.editing.constants import EXPORT_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.errors import RedactionFailed
from squidpdf.editing.info import notice_info
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import Exported, Notice, Saved

__all__ = [
    "ExportController",
    "save_edited",
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
    ) -> Reply[bytes]:
        """The edited PDF, every page or `pages` in that order, and headers for the rest.

        Refuses edits over the limits and pages the document lacks. A
        redaction still in the saved file fails the whole request.
        """
        check_edits(edits)
        if pages is not None:
            check_pages(pages, len(store.load_pages(doc.folder)))
        exported = await self._enqueue_make_pdf(doc.folder, edits=edits, pages=pages)
        return Reply(exported.pdf, reply_headers(exported, said_in))

    async def _enqueue_make_pdf(
        self, folder: Path, *, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        """Make the PDF on a worker."""
        task = partial(make_pdf, str(folder), edits=edits, pages=pages)
        return await self._workers.run(EXPORT_TIMEOUT_S, task)


def make_pdf(folder: str, *, edits: list[Edit], pages: list[int] | None) -> Exported:
    """The document in `folder` with the edits applied and checked, as PDF bytes.

    Runs in a worker.
    """
    path = Path(folder)
    index = store.load_index(path)
    if index is None:  # only a sweep removes it
        raise Gone

    # In the document's folder, so a killed worker's file is swept with it.
    with (
        tempfile.TemporaryDirectory(dir=path) as scratch,
        open_pdf(str(path / store.ORIGINAL)) as engine,
    ):
        out = Path(scratch) / _EXPORTED
        saved = save_edited(engine, index, edits=edits, pages=pages, to=str(out))
        skipped = [skip.edit for skip in saved.applied.skipped]
        # Only the file's own notices: render already said apply's.
        return Exported(out.read_bytes(), skipped, saved.notices)


def save_edited(
    engine: Engine,
    index: SpanIndex,
    *,
    edits: list[Edit],
    pages: list[int] | None = None,
    to: str,
) -> Saved:
    """Apply the edits, keep `pages`, save to `to`, and check the redactions there.

    Raises RedactionFailed, and deletes the file, if a redacted span's text is still in it.
    The same save and check whether a browser downloads the file or the CLI writes it.
    """
    redactions = RedactionController.from_edits(engine, edits, index)
    applied = apply(engine, edits, index)
    notices = [] if pages is None else redactions.keep_pages(engine, pages)
    notices += engine.save(to)
    try:
        redactions.check_saved(to)
    except RedactionFailed:  # the text is still in the file: never leave it lying around
        Path(to).unlink()
        raise
    return Saved(applied, notices)


def reply_headers(exported: Exported, said_in: str) -> dict[str, str]:
    """What export says besides the file: edits left out, notices, and the language."""
    said = [notice_info(Notice(None, message), said_in) for message in exported.notices]
    return {
        _SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped),
        # ASCII only, so a header in any language stays valid.
        _NOTICES_HEADER: json.dumps(said, ensure_ascii=True),
        **words.language_headers(said_in),
    }


def check_pages(pages: list[int], page_count: int) -> None:
    """Refuse a page list export can't give: none, one the document lacks, or one twice."""
    if not pages:
        raise InvalidRequest(debug="pages: empty; leave it out for every page")
    outside = [page for page in pages if not 0 <= page < page_count]
    if outside:
        raise NoSuchPage(debug=f"pages: no page {outside[0]}")
    if len(set(pages)) != len(pages):
        raise InvalidRequest(debug="pages: each page at most once")
