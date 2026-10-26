"""Export: the edits applied to the whole document, and the file sent back.

No web framework here, so a worker can import it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from squidpdf.core import (
    DriverFactory,
    Engine,
    Fidelity,
    InvalidRequest,
    Message,
    Reply,
    SpanIndex,
    Tally,
    Workers,
    tally,
    words,
)
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.free_space import kept_rooms
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply_edits, is_page, page_order, redacted_in, resolve
from squidpdf.editing.constants import EXPORT_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.errors import RedactionFailed
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.replies import notice_info
from squidpdf.editing.types import Exported, FileNotice, Replaced, Saved

_EXPORTED = "export.pdf"  # the file saved and checked by the worker, then read by the server
_PRODUCER = "squidpdf"  # the Producer an exported file names: the program that changed it last

# The body is the file, so these headers carry the rest. The route lists them in the OpenAPI.
SKIPPED_HEADER = "Squid-Skipped-Edits"
NOTICES_HEADER = "Squid-Notices"

# What each replace adds to on the export's line, by how it turned out.
_TALLY_OF: dict[Fidelity, Tally] = {
    "exact": Tally.EDITS_EXACT,
    "substitute": Tally.EDITS_SUBSTITUTE,
    "approximate": Tally.EDITS_APPROXIMATE,
}


@dataclass(frozen=True, slots=True, eq=False)
class ExportController:
    """Export, from request to reply."""

    workers: Workers  # where it makes files, off the server's own thread
    documents: DocumentStore  # where its document is kept, and what opens it

    async def export(
        self, doc: Loaded, *, edits: list[Edit], pages: list[int] | None, language: str
    ) -> Reply[bytes]:
        """The edited PDF, every page or `pages` in that order, and headers for the rest.

        Refuses edits over the limits and pages the document lacks. A
        redaction still in the saved file fails the whole request.
        """
        check_edits(edits)
        if pages is not None:
            # Read and parsed from disk: off the server's thread.
            saved_pages = await asyncio.to_thread(store.load_pages, doc.folder)
            _check_pages(pages, len(saved_pages))
        # The server's folder: a killed worker runs no cleanup, and the edit mustn't outlive the
        # document. Made on the loop, not awaited, so no cancel lands between it and the `try`.
        scratch = Path(tempfile.mkdtemp())
        try:
            exported = await self._enqueue_make_pdf(
                doc.folder, scratch, edits=edits, pages=pages
            )
            # Read here, not sent back: a large file in pebble's one result pipe would hold
            # up every other worker's answer while it crossed.
            pdf = await asyncio.to_thread((scratch / _EXPORTED).read_bytes)
        finally:
            # In a thread: it may hold the whole file. The thread finishes even if the
            # request is cancelled again. Nothing to keep, so a failure is ignored.
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return Reply(pdf, _reply_headers(exported, language))

    async def _enqueue_make_pdf(
        self, folder: Path, scratch: Path, *, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        """Make the PDF on a worker."""
        task = partial(_make_pdf, self.documents, folder, scratch, edits=edits, pages=pages)
        return await self.workers.run(EXPORT_TIMEOUT_S, task)


def _make_pdf(
    documents: DocumentStore,
    folder: Path,
    scratch: Path,
    *,
    edits: list[Edit],
    pages: list[int] | None,
) -> Exported:
    """Save the document in `folder`, the edits applied and checked, into `scratch`.

    The server makes `scratch`, reads the file from it, and removes it. Runs in a worker.
    """
    index = store.require_index(folder)
    rooms = kept_rooms(documents, folder, index)

    with documents.open_original(folder) as engine:
        # Outside the document's folder: deleting it while the file is open can't take the save.
        out = scratch / _EXPORTED
        saved = save_edited(
            engine,
            index,
            rooms=rooms,
            edits=edits,
            pages=pages,
            to=str(out),
            driver_factory=documents.driver_factory,
        )
    _count_fonts_kept(saved.applied.replaced)
    skipped = [skip.edit for skip in saved.applied.skipped]
    # Only the file's own notices: render already said apply's.
    return Exported(skipped_edits=skipped, file_notices=saved.notices)


def _count_fonts_kept(replaced: list[Replaced]) -> None:
    """Tally each replace on the export's line by how it turned out.

    At export, not render: a file downloaded is the edits made, each span's last edit once.
    """
    for each in replaced:
        tally(_TALLY_OF[each.fidelity])


def save_edited(
    engine: Engine,
    index: SpanIndex,
    *,
    rooms: Mapping[str, float],
    edits: list[Edit],
    pages: list[int] | None = None,
    to: str,
    driver_factory: DriverFactory,
) -> Saved:
    """Apply the edits, keep `pages`, save to `to` naming squidpdf, and check the redactions.

    Raises RedactionFailed, and deletes the file, if a redacted span's text is still in it.
    The same save and check whether a browser downloads the file or the CLI writes it.
    `rooms` holds at least each edited span's room, by id, from the original (`Engine.rooms`).
    """
    resolved = resolve(engine, index, rooms=rooms, edits=edits)
    order = page_order(resolved, pages)
    redactions = RedactionController(redacted_in(resolved))
    # Before the edits: a redaction takes its words out of the mark as from every hidden copy.
    engine.mark_changed_by(_PRODUCER, datetime.now(UTC))
    applied = apply_edits(engine, resolved)
    notices: list[Message] = []
    # By their numbers in the original, so before any page is renumbered.
    engine.turn_pages(resolved.turns)
    # Pages left out or moved: renumbered after the last draw, in the order the check reads.
    renumbered = order != list(range(resolved.page_count))
    if renumbered:
        notices += engine.keep_pages(order)
    notices += engine.save(to)
    try:
        redactions.check_saved(to, pages=order, driver_factory=driver_factory)
    except RedactionFailed:  # the text is still in the file: never leave it lying around
        Path(to).unlink()
        raise
    return Saved(applied, notices)


def _reply_headers(exported: Exported, language: str) -> dict[str, str]:
    """What export says besides the file: edits left out, notices, and the language."""
    notices = [notice_info(FileNotice(message), language) for message in exported.file_notices]
    return {
        SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped_edits),
        # ASCII only, so a header in any language stays valid.
        NOTICES_HEADER: json.dumps(notices, ensure_ascii=True),
        **words.language_headers(language),
    }


def _check_pages(pages: list[int], page_count: int) -> None:
    """Refuse a page list export can't give: none, one the document lacks, or one twice."""
    if not pages:
        raise InvalidRequest(debug="pages: empty; leave it out for every page")
    outside = [page for page in pages if not is_page(page, page_count)]
    if outside:
        raise NoSuchPage(debug=f"pages: no page {outside[0]}")
    if len(set(pages)) != len(pages):
        raise InvalidRequest(debug="pages: each page at most once")
