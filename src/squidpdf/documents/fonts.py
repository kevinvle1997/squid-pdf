from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import BinaryIO

from squidpdf.core import Problem, Reply, Workers, strip_subset, words
from squidpdf.documents import attached, constants, disk, store
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
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import KeptAnalysis, Loaded

_REFUSALS: dict[str, type[Problem]] = {
    "attached_unreadable": NotAFont,
    "font_no_width_list": FontUnchecked,
}


@dataclass(frozen=True, slots=True, eq=False)
class AttachController:
    workers: Workers
    documents: DocumentStore

    async def attach(
        self,
        doc: Loaded,
        *,
        font_name: str,
        declared: int | None,
        chunks: AsyncIterable[bytes],
        language: str,
    ) -> Reply[bytes]:
        name = strip_subset(font_name)
        # Read as module attributes, so a test can lower the limits.
        declared_too_large = declared is not None and declared > constants.MAX_FONT_BYTES
        if declared_too_large:
            raise TooLarge(constants.MAX_FONT_MB)
        files = await asyncio.to_thread(attached.attached_files, doc.folder)
        if not files.can_keep(name):
            raise TooManyFonts(constants.MAX_FONTS)
        arriving = await asyncio.to_thread(attached.arriving_font, doc.folder)
        try:
            async with disk.disk_held(declared, most=constants.MAX_FONT_BYTES):
                await _save_font(chunks, to=arriving)
            kept = await self._enqueue_attach_font(doc.folder, name=name, arrived=arriving)
        finally:
            await asyncio.to_thread(arriving.unlink, missing_ok=True)
        return await _document_reply(doc, kept=kept, language=language)

    async def _enqueue_attach_font(
        self, folder: Path, *, name: str, arrived: Path
    ) -> KeptAnalysis:
        task = partial(_attach_font, self.documents, folder, name=name, arrived=arrived)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


@dataclass(frozen=True, slots=True, eq=False)
class DetachController:
    workers: Workers
    documents: DocumentStore

    async def detach(self, doc: Loaded, *, font_name: str, language: str) -> Reply[bytes]:
        kept = await self._enqueue_detach_font(doc.folder, name=strip_subset(font_name))
        return await _document_reply(doc, kept=kept, language=language)

    async def _enqueue_detach_font(self, folder: Path, *, name: str) -> KeptAnalysis:
        task = partial(_detach_font, self.documents, folder, name=name)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _attach_font(
    documents: DocumentStore, folder: Path, *, name: str, arrived: Path
) -> KeptAnalysis:
    index = store.require_index(folder)
    span = next((span for span in index if strip_subset(span.font) == name), None)
    if span is None:
        raise NoSuchFont()
    font_file = arrived.read_bytes()
    with attached.fonts_locked(folder):
        # Counted again under the lock: two attaches at once each passed the count before it.
        if not attached.attached_files(folder).can_keep(name):
            raise TooManyFonts(constants.MAX_FONTS)
        with documents.open_original(folder) as engine:
            why = engine.why_not_its_font(span, font_file)
        if why is not None:
            raise _REFUSALS.get(why.key, FontMismatch)(debug=why.key)
        attached.keep_font(folder, name, arrived)
        return analyse(documents, folder)


def _detach_font(documents: DocumentStore, folder: Path, *, name: str) -> KeptAnalysis:
    with attached.fonts_locked(folder):
        attached.drop_font(folder, name)
        return analyse(documents, folder)


async def _document_reply(doc: Loaded, *, kept: KeptAnalysis, language: str) -> Reply[bytes]:
    body = await asyncio.to_thread(
        document_json, doc.id, expires_at=doc.expires_at, kept=kept, language=language
    )
    return Reply(body, words.language_headers(language))


async def _save_font(chunks: AsyncIterable[bytes], *, to: Path) -> None:
    size = 0
    out = await asyncio.to_thread(_opened_to_write, to)
    try:
        with disk.full_disk_refused():
            async for chunk in chunks:
                size += len(chunk)
                # Read as a module attribute, so a test can lower the limit.
                if size > constants.MAX_FONT_BYTES:
                    raise TooLarge(constants.MAX_FONT_MB)
                await asyncio.to_thread(out.write, chunk)
    finally:
        await asyncio.to_thread(out.close)


def _opened_to_write(path: Path) -> BinaryIO:
    return path.open("wb")
