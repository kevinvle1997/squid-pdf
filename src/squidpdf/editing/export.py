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
from functools import partial
from pathlib import Path

from squidpdf.core import Engine, InvalidRequest, Message, Reply, SpanIndex, Workers, words
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.rooms import kept_rooms
from squidpdf.documents.types import Loaded
from squidpdf.editing.apply import apply_edits, is_page, page_order, redacted_in, resolve
from squidpdf.editing.constants import EXPORT_TIMEOUT_S
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.errors import RedactionFailed
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.replies import notice_info
from squidpdf.editing.types import Exported, FileNotice, Saved

_EXPORTED = "export.pdf"  # the file saved and checked by the worker, then read by the server

# The body is the file, so these headers carry the rest. The route lists them in the OpenAPI.
SKIPPED_HEADER = "Squid-Skipped-Edits"
NOTICES_HEADER = "Squid-Notices"


@dataclass(frozen=True, slots=True, eq=False)
class ExportController:
    """Export, from request to reply."""

    workers: Workers  # where it makes files, off the server's own thread

    async def export(
        self, doc: Loaded, *, edits: list[Edit], pages: list[int] | None, said_in: str
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
        # The server's folder, not the worker's: a worker killed at its timeout
        # runs no cleanup, and the edited file must not outlive the document.
        # Made here on the loop, one mkdir: an await could be cancelled after it
        # exists and before the `try` that removes it.
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
        return Reply(pdf, _reply_headers(exported, said_in))

    async def _enqueue_make_pdf(
        self, folder: Path, scratch: Path, *, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        """Make the PDF on a worker."""
        task = partial(_make_pdf, folder, scratch, edits=edits, pages=pages)
        return await self.workers.run(EXPORT_TIMEOUT_S, task)


def _make_pdf(
    folder: Path, scratch: Path, *, edits: list[Edit], pages: list[int] | None
) -> Exported:
    """Save the document in `folder`, the edits applied and checked, into `scratch`.

    The server makes `scratch`, reads the file from it, and removes it. Runs in a worker.
    """
    index = store.require_index(folder)
    rooms = kept_rooms(folder, index)

    with store.open_original(folder) as engine:
        # Outside the document's folder: deleting it while the file is open can't take the save.
        out = scratch / _EXPORTED
        saved = save_edited(engine, index, rooms=rooms, edits=edits, pages=pages, to=str(out))
        skipped = [skip.edit for skip in saved.applied.skipped]
        # Only the file's own notices: render already said apply's.
        return Exported(skipped_edits=skipped, file_notices=saved.notices)


def save_edited(
    engine: Engine,
    index: SpanIndex,
    *,
    rooms: Mapping[str, float],
    edits: list[Edit],
    pages: list[int] | None = None,
    to: str,
) -> Saved:
    """Apply the edits, keep `pages`, save to `to`, and check the redactions there.

    Raises RedactionFailed, and deletes the file, if a redacted span's text is still in it.
    The same save and check whether a browser downloads the file or the CLI writes it.
    `rooms` holds at least each edited span's room, by id, from the original (`Engine.rooms`).
    """
    resolved = resolve(engine, index, rooms=rooms, edits=edits)
    order = page_order(resolved, pages)
    redactions = RedactionController(redacted_in(resolved))
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
        redactions.check_saved(to, pages=order)
    except RedactionFailed:  # the text is still in the file: never leave it lying around
        Path(to).unlink()
        raise
    return Saved(applied, notices)


def _reply_headers(exported: Exported, said_in: str) -> dict[str, str]:
    """What export says besides the file: edits left out, notices, and the language."""
    notices = [notice_info(FileNotice(message), said_in) for message in exported.file_notices]
    return {
        SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped_edits),
        # ASCII only, so a header in any language stays valid.
        NOTICES_HEADER: json.dumps(notices, ensure_ascii=True),
        **words.language_headers(said_in),
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
