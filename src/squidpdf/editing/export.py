"""Export: the edits applied to the whole document, and the file sent back.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from functools import partial
from pathlib import Path

from squidpdf.core import Engine, InvalidRequest, Message, Reply, SpanIndex, Workers, words
from squidpdf.documents import store
from squidpdf.documents.errors import Gone, NoSuchPage
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply_edits, page_order, redacted_in, resolve
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
        # The server's folder, not the worker's: a worker killed at its timeout
        # runs no cleanup, and the edited file must not outlive the document.
        # Made here on the loop, one mkdir: an await could be cancelled after it
        # exists and before the `try` that removes it.
        scratch = Path(tempfile.mkdtemp())
        try:
            exported = await self._enqueue_make_pdf(
                doc.folder, scratch, edits=edits, pages=pages
            )
        finally:
            # In a thread: it may hold the whole file. The thread finishes even if the
            # request is cancelled again. Nothing to keep, so a failure is ignored.
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return Reply(exported.pdf, reply_headers(exported, said_in))

    async def _enqueue_make_pdf(
        self, folder: Path, scratch: Path, *, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        """Make the PDF on a worker."""
        task = partial(make_pdf, str(folder), str(scratch), edits=edits, pages=pages)
        return await self._workers.run(EXPORT_TIMEOUT_S, task)


def make_pdf(
    folder: str, scratch: str, *, edits: list[Edit], pages: list[int] | None
) -> Exported:
    """The document in `folder` with the edits applied and checked, as PDF bytes.

    Saves into `scratch`, which the server makes and removes. Runs in a worker.
    """
    path = Path(folder)
    index = store.load_index(path)
    if index is None:  # analysed at upload, so a sweep or a delete removed it
        raise Gone

    with store.open_original(path) as engine:
        # Outside the document's folder: deleting it while the file is open can't take the save.
        out = Path(scratch) / _EXPORTED
        saved = save_edited(engine, index, edits=edits, pages=pages, to=str(out))
        skipped = [skip.edit for skip in saved.applied.skipped]
        # Only the file's own notices: render already said apply's.
        return Exported(out.read_bytes(), skipped_edits=skipped, file_notices=saved.notices)


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
    resolved = resolve(engine, edits, index)
    order = page_order(resolved, pages)
    redactions = RedactionController(redacted_in(resolved))
    applied = apply_edits(engine, resolved)
    notices: list[Message] = []
    # Pages left out or moved: renumbered after the last draw, in the order the check reads.
    renumbered = order != list(range(resolved.page_count))
    if renumbered:
        notices += engine.keep_pages(order)
    notices += engine.save(to)
    try:
        redactions.check_saved(to, pages=order)
    except RedactionFailed:  # the text is still in the file: never leave it lying around
        Path(to).unlink()
        raise
    return Saved(applied, notices)


def reply_headers(exported: Exported, said_in: str) -> dict[str, str]:
    """What export says besides the file: edits left out, notices, and the language."""
    said = [notice_info(Notice(None, message), said_in) for message in exported.file_notices]
    return {
        _SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped_edits),
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
