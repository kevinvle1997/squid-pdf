"""Read: the document under this build, in the reader's words. No web framework here."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from pathlib import Path

import orjson
import xxhash

from squidpdf.core import BUILD, Reply, Workers, words
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse, kept_analysis
from squidpdf.documents.constants import ANALYSE_TIMEOUT_S, DOCUMENT_CACHE, MAX_PAGES
from squidpdf.documents.replies import document_json, time_of
from squidpdf.documents.types import Analysis, KeptAnalysis, Loaded

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
        kept = await self._saved_analysis(doc)
        # A thousand pages' analysis takes a while to hash and write: off the server's thread.
        return await asyncio.to_thread(
            _answer, doc, kept=kept, said_in=said_in, if_none_match=if_none_match
        )

    async def _saved_analysis(self, doc: Loaded) -> KeptAnalysis:
        """The analysis kept under this build, worked out first if the build is new."""
        kept = await asyncio.to_thread(store.load_analysis, doc.folder, BUILD)
        if kept is None:  # a new build: worked out again over the saved index
            analysis = await self._enqueue_analyse(doc.folder)
            # Every span written out: off the server's thread, as the answer is.
            kept = await asyncio.to_thread(kept_analysis, analysis)
        return kept

    async def _enqueue_analyse(self, folder: Path) -> Analysis:
        """Analyse the document on a worker."""
        task = partial(analyse, str(folder), MAX_PAGES)
        return await self.workers.run(ANALYSE_TIMEOUT_S, task)


def _answer(
    doc: Loaded, *, kept: KeptAnalysis, said_in: str, if_none_match: str | None
) -> Reply[bytes]:
    """The analysis kept, as the browser gets it: the JSON with its ETag, or a 304."""
    headers = {
        "ETag": _etag_of(kept, said_in),
        "Cache-Control": DOCUMENT_CACHE,
        EXPIRES_HEADER: time_of(doc.expires_at),
        **words.language_headers(said_in),
    }
    # The browser's copy is current: a 304 carries no body.
    if if_none_match == headers["ETag"]:
        return Reply(b"", headers, HTTPStatus.NOT_MODIFIED)
    body = document_json(doc.id, expires_at=doc.expires_at, kept=kept, said_in=said_in)
    return Reply(body, headers)


def _etag_of(kept: KeptAnalysis, said_in: str) -> str:
    """The document's ETag: over the analysis as kept and the words it's said in.

    Not `expires_at`, which moves on every visit. Another language, or a
    sentence reworded since, is another body.
    """
    said = orjson.dumps([said_in, words.catalog(said_in)])
    return f'"{xxhash.xxh3_64_hexdigest(kept.facts + kept.spans + said)}"'
