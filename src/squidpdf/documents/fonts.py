"""The user's own copies of a document's fonts: attached, checked, removed. No web framework.

A copy is kept in the document's folder, so it goes with the document, and every span is
judged again with it: the reply is the Document.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import BinaryIO

from squidpdf.core import Problem, Reply, Workers, strip_subset, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.errors import (
    FontMismatch,
    FontUnchecked,
    NoSuchFont,
    NotAFont,
    TooLarge,
    TooManyFonts,
)
from squidpdf.documents.replies import document_json
from squidpdf.documents.types import KeptAnalysis, Loaded

# Why the engine turned a copy away, by its reason, as the Problem that tells the user; any
# other reason (another width, too few letters shared) is a mismatch.
_REFUSALS: dict[str, type[Problem]] = {
    "attached_unreadable": NotAFont,
    "font_no_width_list": FontUnchecked,
}


@dataclass(frozen=True, slots=True, eq=False)
class AttachController:
    """Attaching the user's copy of a font, from request to reply."""

    workers: Workers  # where it's checked and the document judged again

    async def attach(
        self,
        doc: Loaded,
        *,
        font_name: str,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        said_in: str,
    ) -> Reply[bytes]:
        """Keep `chunks` as the copy of `font_name`, if it's that font; judge every span again.

        `declared` is the size the request states. Refuses a file too large, one
        more copy than a document keeps, and a file that isn't the font, keeping nothing.
        """
        name = strip_subset(font_name)
        # Read as module attributes, so a test can lower the limits.
        declared_too_large = declared is not None and declared > constants.MAX_FONT_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FONT_MB)
        attached = await asyncio.to_thread(store.attached_files, doc.folder)
        one_too_many = name not in attached and len(attached.paths) >= constants.MAX_FONTS
        if one_too_many:
            raise TooManyFonts(constants.MAX_FONTS)
        arriving = await asyncio.to_thread(store.arriving_font, doc.folder)
        try:
            await _save_font(chunks, to=arriving)
            kept = await self._enqueue_attach_font(doc.folder, name=name, arrived=arriving)
        finally:
            # Kept under its name by now, or refused: either way, nothing to keep here.
            await asyncio.to_thread(arriving.unlink, missing_ok=True)
        return await _document_reply(doc, kept=kept, said_in=said_in)

    async def _enqueue_attach_font(
        self, folder: Path, *, name: str, arrived: Path
    ) -> KeptAnalysis:
        """Check and keep the copy, and judge the document again, on a worker."""
        task = partial(_attach_font, str(folder), name=name, arrived=str(arrived))
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


@dataclass(frozen=True, slots=True, eq=False)
class DetachController:
    """Removing the user's copy of a font, from request to reply."""

    workers: Workers  # where the document is judged again

    async def detach(self, doc: Loaded, *, font_name: str, said_in: str) -> Reply[bytes]:
        """Remove the copy of `font_name`, if there is one, and judge every span again."""
        kept = await self._enqueue_detach_font(doc.folder, name=strip_subset(font_name))
        return await _document_reply(doc, kept=kept, said_in=said_in)

    async def _enqueue_detach_font(self, folder: Path, *, name: str) -> KeptAnalysis:
        """Remove the copy, and judge the document again, on a worker."""
        task = partial(_detach_font, str(folder), name=name)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _attach_font(folder: str, *, name: str, arrived: str) -> KeptAnalysis:
    """Check the copy that `arrived` is the font `name`, keep it, and judge again. In a worker.

    Raises NoSuchFont for a font the document doesn't use, and FontMismatch for a
    file that isn't it: checked against the file's copies, or its width list.
    """
    path = Path(folder)
    index = store.require_index(path)
    # The first span in the font speaks for it, as in the analysis.
    span = next((span for span in index if strip_subset(span.font) == name), None)
    if span is None:
        raise NoSuchFont()
    font_file = Path(arrived).read_bytes()
    with store.fonts_locked(path):
        # Counted again under the lock: two attaches at once each passed the count before it.
        attached = store.attached_files(path)
        if name not in attached and len(attached.paths) >= constants.MAX_FONTS:
            raise TooManyFonts(constants.MAX_FONTS)
        with store.open_original(path) as engine:
            why = engine.why_not_its_font(span, font_file)
        if why is not None:
            raise _REFUSALS.get(why.key, FontMismatch)(debug=why.key)
        store.keep_font(path, name, Path(arrived))
        return analyse(folder)


def _detach_font(folder: str, *, name: str) -> KeptAnalysis:
    """Remove the user's copy of the font `name`, and judge the document again. In a worker."""
    path = Path(folder)
    with store.fonts_locked(path):
        store.drop_font(path, name)
        return analyse(folder)


async def _document_reply(doc: Loaded, *, kept: KeptAnalysis, said_in: str) -> Reply[bytes]:
    """The document judged again, as the browser gets it, in `said_in`."""
    # Off the server's thread: it reads every font's letters and writes them out again.
    body = await asyncio.to_thread(
        document_json, doc.id, expires_at=doc.expires_at, kept=kept, said_in=said_in
    )
    return Reply(body, words.language_headers(said_in))


async def _save_font(chunks: AsyncIterable[bytes], *, to: Path) -> None:
    """Stream the copy to `to`, refused as soon as it's too big. Written off the loop."""
    size = 0
    out = await asyncio.to_thread(_opened_to_write, to)
    try:
        with store.full_disk_refused():
            async for chunk in chunks:
                size += len(chunk)
                # Read as a module attribute, so a test can lower the limit.
                if size > constants.MAX_FONT_BYTES:
                    raise TooLarge(constants.MAX_FONT_MB)
                await asyncio.to_thread(out.write, chunk)
    finally:  # closed, refused or not; the caller removes what's refused
        await asyncio.to_thread(out.close)


def _opened_to_write(path: Path) -> BinaryIO:
    """`path`, opened to be written from its start."""
    return path.open("wb")
