"""Read: the document under this build, in the reader's words. No web framework here."""

from __future__ import annotations

from functools import partial
from http import HTTPStatus
from pathlib import Path

import orjson
import xxhash

from squidpdf.core import BUILD, Reply, Workers, words
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.constants import ANALYSE_TIMEOUT_S, DOCUMENT_CACHE, MAX_PAGES
from squidpdf.documents.info import document_response
from squidpdf.documents.types import Analysis, Loaded

__all__ = [
    "ReadController",
]


class ReadController:
    """Read, from request to reply."""

    def __init__(self, workers: Workers) -> None:
        """Analyse on `workers`, off the server's own thread."""
        self._workers = workers

    async def read(
        self, doc: Loaded, *, said_in: str, if_none_match: str | None
    ) -> Reply[bytes]:
        """The document as JSON with its ETag, or a 304 when `if_none_match` is that ETag."""
        saved = await self._saved_analysis(doc)
        headers = {
            "ETag": etag_of(saved, said_in),
            "Cache-Control": DOCUMENT_CACHE,
            **words.language_headers(said_in),
        }
        # The browser's copy is current: a 304 carries no body, so no body's type.
        if if_none_match == headers["ETag"]:
            return Reply(b"", headers, HTTPStatus.NOT_MODIFIED)
        body = document_response(
            doc.id, expires_at=doc.expires_at, analysis=orjson.loads(saved), said_in=said_in
        )
        typed = headers | {"Content-Type": "application/json"}
        return Reply(orjson.dumps(body), typed)

    async def _saved_analysis(self, doc: Loaded) -> bytes:
        """The analysis kept under this build, worked out first if the build is new."""
        saved = store.load_analysis(doc.folder, BUILD)
        if saved is None:  # a new build: worked out again over the saved index
            saved = orjson.dumps(await self._enqueue_analyse(doc.folder))
        return saved

    async def _enqueue_analyse(self, folder: Path) -> Analysis:
        """Analyse the document on a worker."""
        task = partial(analyse, str(folder), MAX_PAGES)
        return await self._workers.run(ANALYSE_TIMEOUT_S, task)


def etag_of(saved: bytes, said_in: str) -> str:
    """The document's ETag: over the analysis as kept and the words it's said in.

    Not `expires_at`, which moves on every visit. Another language, or a
    sentence reworded since, is another body.
    """
    said = orjson.dumps([said_in, words.catalog(said_in)])
    return f'"{xxhash.xxh3_64_hexdigest(saved + said)}"'
