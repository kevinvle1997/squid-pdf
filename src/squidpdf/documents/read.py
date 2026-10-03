"""Read: the document under this build, in the reader's words. No web framework here."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

import xxhash

from squidpdf.core import BUILD, Reply, Workers, words
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.constants import ANALYSE_TIMEOUT_S, DOCUMENT_CACHE, MAX_PAGES
from squidpdf.documents.replies import document_json, reply_digest, time_of
from squidpdf.documents.types import KeptAnalysis, Loaded

# When the document now expires: a 304 carries no body, and reading restarted the hour.
# The route lists it in the OpenAPI.
EXPIRES_HEADER = "Squid-Expires-At"


@dataclass(frozen=True, slots=True, eq=False)
class ReadController:
    """Read, from request to reply."""

    workers: Workers  # where it analyses, off the server's own thread

    async def read(
        self, doc: Loaded, *, said_in: str, if_none_match: str | None
    ) -> Reply[bytes]:
        """The document as JSON with its ETag, or a 304 when `if_none_match` is that ETag.

        Either way with when it now expires, which the ETag leaves out: it moves
        on every visit, and a 304's browser keeps the body it had.
        """
        digest = await asyncio.to_thread(store.load_analysis_digest, doc.folder, BUILD)
        etag = None if digest is None else _etag_of(digest, said_in)
        # The browser's copy is current: a 304 carries no body, so the analysis isn't read.
        if etag is not None and if_none_match == etag:
            return Reply(b"", _headers_of(doc, etag, said_in), HTTPStatus.NOT_MODIFIED)
        kept = await self._saved_analysis(doc)
        # The fonts' letters are read and written out again: off the server's thread.
        body = await asyncio.to_thread(
            document_json, doc.id, expires_at=doc.expires_at, kept=kept, said_in=said_in
        )
        return Reply(body, _headers_of(doc, _etag_of(kept.digest, said_in), said_in))

    async def _saved_analysis(self, doc: Loaded) -> KeptAnalysis:
        """The analysis kept, worked out first if the build or the tuning is new."""
        kept = await asyncio.to_thread(store.load_analysis, doc.folder, BUILD)
        if kept is None:  # a new build or tuning: worked out again over the saved index
            kept = await self._enqueue_analyse(doc.folder)
        return kept

    async def _enqueue_analyse(self, folder: Path) -> KeptAnalysis:
        """Analyse the document on a worker."""
        task = partial(analyse, str(folder), MAX_PAGES)
        return await self.workers.run(ANALYSE_TIMEOUT_S, task)


def _headers_of(doc: Loaded, etag: str, said_in: str) -> dict[str, str]:
    """The headers of a read, 200 or 304: its ETag, how to cache it, and when it expires."""
    return {
        "ETag": etag,
        "Cache-Control": DOCUMENT_CACHE,
        EXPIRES_HEADER: time_of(doc.expires_at),
        **words.language_headers(said_in),
    }


def _etag_of(digest: str, said_in: str) -> str:
    """The document's ETag: over the analysis's `digest` and the rest the reply is made of.

    That is the words it's said in, the fit rules and the reply's shape. Not
    `expires_at`, which moves on every visit. Another language, a sentence
    reworded since, or a fit rule retuned, is another body.
    """
    return f'"{xxhash.xxh3_64_hexdigest((digest + reply_digest(said_in)).encode())}"'
