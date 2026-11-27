"""No web framework here, so a worker can import it."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from io import BufferedReader
from pathlib import Path

from squidpdf.core import (
    DriverFactory,
    Engine,
    Fidelity,
    FreeSpace,
    InvalidRequest,
    LogController,
    LogEvent,
    Message,
    OwnText,
    Reply,
    SpanIndex,
    Tally,
    Workers,
    loggable_family,
    tally,
    words,
)
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage, is_page
from squidpdf.documents.free_space import kept_free_space
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import Loaded
from squidpdf.editing import constants
from squidpdf.editing.apply import (
    apply_edits,
    edited_by,
    page_order,
    planned_of,
    redacted_in,
    resolve,
)
from squidpdf.editing.edits import Edit, check_edits
from squidpdf.editing.errors import (
    RedactionCovered,
    RedactionFailed,
    RedactionMoved,
    RedactionUnchecked,
)
from squidpdf.editing.redaction import redactions_of
from squidpdf.editing.replies import notice_info
from squidpdf.editing.types import Exported, FileNotice, Replaced, Saved

_EXPORTED = "export.pdf"
_PRODUCER = "squidpdf"
_UNLISTED = "unlisted"

# The body is the file, so these headers carry the rest.
SKIPPED_HEADER = "Squid-Skipped-Edits"
NOTICES_HEADER = "Squid-Notices"

_log = LogController.for_module(__name__)

_TALLY_OF: dict[Fidelity, Tally] = {
    "exact": Tally.EDITS_EXACT,
    "substitute": Tally.EDITS_SUBSTITUTE,
    "approximate": Tally.EDITS_APPROXIMATE,
}


@dataclass(frozen=True, slots=True, eq=False)
class ExportController:
    workers: Workers
    documents: DocumentStore

    async def export(
        self, doc: Loaded, *, edits: list[Edit], pages: list[int] | None, language: str
    ) -> Reply[BufferedReader]:
        check_edits(edits)
        if pages is not None:
            saved_pages = await asyncio.to_thread(store.load_pages, doc.folder)
            _check_pages(pages, len(saved_pages))
        # A killed worker runs no cleanup. Made on the loop, so no cancel lands before `try`.
        scratch = Path(tempfile.mkdtemp())
        try:
            exported = await self._enqueue_make_pdf(
                doc.folder, scratch, edits=edits, pages=pages
            )
            # Opened here, not sent back: a large file in pebble's result pipe blocks the rest.
            pdf = await asyncio.to_thread(_opened_to_read, scratch / _EXPORTED)
        finally:
            # In a thread, as it may hold the whole file. An open file outlives its folder.
            await asyncio.to_thread(shutil.rmtree, scratch, ignore_errors=True)
        return Reply(pdf, _reply_headers(exported, language))

    async def _enqueue_make_pdf(
        self, folder: Path, scratch: Path, *, edits: list[Edit], pages: list[int] | None
    ) -> Exported:
        task = partial(_make_pdf, self.documents, folder, scratch, edits=edits, pages=pages)
        return await self.workers.run(constants.EXPORT_TIMEOUT_S, task)


def _opened_to_read(path: Path) -> BufferedReader:
    return path.open("rb")


def _make_pdf(
    documents: DocumentStore,
    folder: Path,
    scratch: Path,
    *,
    edits: list[Edit],
    pages: list[int] | None,
) -> Exported:
    index = store.require_index(folder)
    free_space = kept_free_space(documents, folder, index)

    with documents.open_original(folder) as engine:
        # Outside the document's folder: deleting it while the file is open can't take the save.
        out = scratch / _EXPORTED
        saved = save_edited(
            engine,
            index,
            free_space=free_space,
            edits=edits,
            pages=pages,
            to=str(out),
            driver_factory=documents.driver_factory,
        )
    _tally_by_fidelity(saved.applied.replaced)
    skipped = [skip.position for skip in saved.applied.skipped]
    return Exported(skipped_edits=skipped, file_notices=saved.notices)


def _tally_by_fidelity(replaced: list[Replaced]) -> None:
    """At export, not render: a file downloaded is the edits made, each span's last once."""
    for outcome in replaced:
        tally(_TALLY_OF[outcome.verdict.fidelity])
        if outcome.verdict.fidelity == "substitute":
            _log_substitute(outcome)


def _log_substitute(outcome: Replaced) -> None:
    loggable = loggable_family(outcome.shown_name)
    font = OwnText(_UNLISTED if loggable is None else loggable)
    substitute = outcome.verdict.substitute
    face = OwnText(substitute.face.file)
    same_widths = substitute.same_widths
    # The font gives a letter typed no width: no number, rather than a wrong one.
    if substitute.width_delta_pct is None:
        _log.write(LogEvent.EDIT_IN_SUBSTITUTE, font=font, face=face, same_widths=same_widths)
        return
    _log.write(
        LogEvent.EDIT_IN_SUBSTITUTE,
        font=font,
        face=face,
        same_widths=same_widths,
        width_delta_pct=substitute.width_delta_pct,
    )


def save_edited(
    engine: Engine,
    index: SpanIndex,
    *,
    free_space: FreeSpace,
    edits: list[Edit],
    pages: list[int] | None = None,
    to: str,
    driver_factory: DriverFactory,
) -> Saved:
    resolved = resolve(engine, index, free_space=free_space, edits=edits)
    order = page_order(resolved, pages)
    # Before the edits: erasing can drop the fonts new text is measured with.
    planned = planned_of(engine, resolved)
    edited = edited_by(planned.steps)
    redactions = redactions_of(engine, resolved, redacted_in(resolved), edited=edited)
    # Before the edits: a redaction takes its words out of the mark as from every hidden copy.
    engine.mark_changed_by(_PRODUCER, datetime.now(UTC))
    applied = apply_edits(engine, resolved, planned)
    notices: list[Message] = []
    engine.turn_pages(resolved.turns)
    renumbered = order != list(range(resolved.page_count))
    if renumbered:
        notices += engine.keep_pages(order)
    notices += engine.save(to)
    try:
        redactions.check_saved(to, pages=order, driver_factory=driver_factory)
    except RedactionFailed, RedactionCovered, RedactionMoved, RedactionUnchecked:
        Path(to).unlink()
        raise
    return Saved(applied, notices)


def _reply_headers(exported: Exported, language: str) -> dict[str, str]:
    notices = [notice_info(FileNotice(message), language) for message in exported.file_notices]
    return {
        SKIPPED_HEADER: ", ".join(str(position) for position in exported.skipped_edits),
        # ASCII only, so a header in any language stays valid.
        NOTICES_HEADER: json.dumps(notices, ensure_ascii=True),
        **words.language_headers(language),
    }


def _check_pages(pages: list[int], page_count: int) -> None:
    if not pages:
        raise InvalidRequest(debug="pages: empty; leave it out for every page")
    outside = [page for page in pages if not is_page(page, page_count)]
    if outside:
        raise NoSuchPage(debug=f"pages: no page {outside[0]}")
    if len(set(pages)) != len(pages):
        raise InvalidRequest(debug="pages: each page at most once")
