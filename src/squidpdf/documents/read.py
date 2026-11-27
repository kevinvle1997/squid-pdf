from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

import xxhash

from squidpdf.core import BUILD, Reply, Tally, Workers, tally, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.replies import document_json, reply_digest, time_of
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import KeptAnalysis, Loaded

# A 304 carries no body, and reading restarted the hour.
EXPIRES_HEADER = "Squid-Expires-At"


@dataclass(frozen=True, slots=True, eq=False)
class ReadController:
    workers: Workers
    documents: DocumentStore

    async def read(
        self, doc: Loaded, *, language: str, if_none_match: str | None
    ) -> Reply[bytes]:
        """The ETag leaves out the expiry, which moves each visit: a 304 keeps its body."""
        digest = await asyncio.to_thread(store.load_analysis_digest, doc.folder, BUILD)
        etag = None if digest is None else _etag_of(digest, language)
        if etag is not None and if_none_match == etag:
            return Reply(b"", _headers_of(doc, etag, language), HTTPStatus.NOT_MODIFIED)
        kept = await self._saved_analysis(doc)
        body = await asyncio.to_thread(
            document_json, doc.id, expires_at=doc.expires_at, kept=kept, language=language
        )
        return Reply(body, _headers_of(doc, _etag_of(kept.digest, language), language))

    async def _saved_analysis(self, doc: Loaded) -> KeptAnalysis:
        kept = await asyncio.to_thread(store.load_analysis, doc.folder, BUILD)
        if kept is None:
            tally(Tally.REANALYSED)
            kept = await self._enqueue_analyse(doc.folder)
        return kept

    async def _enqueue_analyse(self, folder: Path) -> KeptAnalysis:
        task = partial(analyse, self.documents, folder)
        return await self.workers.run(constants.ANALYSE_TIMEOUT_S, task)


def _headers_of(doc: Loaded, etag: str, language: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": constants.DOCUMENT_CACHE,
        EXPIRES_HEADER: time_of(doc.expires_at),
        **words.language_headers(language),
    }


def _etag_of(digest: str, language: str) -> str:
    return f'"{xxhash.xxh3_64_hexdigest((digest + reply_digest(language)).encode())}"'
