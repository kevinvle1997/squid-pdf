"""Upload a document, read it, delete it, and view its pages.

Also `load`, the dependency every route on a document starts with: editing
reuses it. Routes stay thin: owner, validate, pool, reply.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Annotated

import orjson
import xxhash
from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits
from squidpdf.api import owner, pool
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, NotFound, words
from squidpdf.documents import constants, store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.constants import DOCUMENT_CACHE, SWEEP_EVERY_S
from squidpdf.documents.info import document_response
from squidpdf.documents.pages import PageController
from squidpdf.documents.types import Document, Loaded
from squidpdf.documents.upload import UploadController

__all__ = [
    "router",
    "load",
    "upload_controller",
    "upload",
    "read",
    "delete",
    "page_controller",
    "page",
    "sweep_forever",
]

router = APIRouter(prefix="/api/documents")

_logger = logging.getLogger(__name__)


def load(doc_id: str, request: Request) -> Loaded:
    """This browser's document, or not_found for any other: missing, expired or not theirs."""
    found = store.find(doc_id)
    if found is None:
        raise NotFound()
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, store.touch(folder))


def upload_controller(workers: Annotated[Pool, Depends(pool.current)]) -> UploadController:
    """Upload's controller, on the app's workers."""
    return UploadController(workers)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Document)
async def upload(
    request: Request,
    *,
    token: Annotated[str, Depends(owner.token)],
    controller: Annotated[UploadController, Depends(upload_controller)],
    said_in: ReaderLanguage,
    response: Response,
) -> Document:
    """A raw PDF body, no multipart and no filename. Answers with every span judged."""
    declared = request.headers.get("content-length")  # absent when the body is chunked
    reply = await controller.upload(
        owner.digest(token),
        declared=None if declared is None else int(declared),
        chunks=request.stream(),
        said_in=said_in,
    )
    # Data, not a Response: FastAPI adds the new owner cookie only to a reply it makes.
    response.headers.update(reply.headers)
    return reply.body


@router.get("/{doc_id}", response_model=Document)
async def read(
    doc: Annotated[Loaded, Depends(load)],
    *,
    request: Request,
    response: Response,
    workers: Annotated[Pool, Depends(pool.current)],
    said_in: ReaderLanguage,
) -> Document | Response:
    """The document, in the reader's language. Worked out again only for a new `build`."""
    raw = store.load_analysis(doc.folder, BUILD)
    if raw is None:
        task = partial(analyse, str(doc.folder), constants.MAX_PAGES)
        analysis = await workers.run(constants.UPLOAD_TIMEOUT_S, task)
        raw = orjson.dumps(analysis)
    else:
        analysis = orjson.loads(raw)
    # Over the analysis and the words it's said in; not `expires_at`, which moves
    # on every visit and is a hint. The analysis is in no language, so the words
    # too: another language, or a sentence reworded since, is another body.
    said = orjson.dumps([said_in, words.catalog(said_in)])
    etag = f'"{xxhash.xxh3_64_hexdigest(raw + said)}"'
    headers = {"ETag": etag, "Cache-Control": DOCUMENT_CACHE, **words.language_headers(said_in)}
    if request.headers.get("if-none-match") == etag:  # absent on a first read
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return document_response(
        doc.id, expires_at=doc.expires_at, analysis=analysis, said_in=said_in
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(doc: Annotated[Loaded, Depends(load)]) -> None:
    """The document and everything worked out from it, now rather than in an hour."""
    store.delete(doc.folder)


def page_controller(workers: Annotated[Pool, Depends(pool.current)]) -> PageController:
    """The page image's controller, on the app's workers."""
    return PageController(workers)


@router.get(
    "/{doc_id}/pages/{n}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def page(
    doc: Annotated[Loaded, Depends(load)],
    *,
    n: int,
    scale: Annotated[int, Query(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))],
    build: str,
    controller: Annotated[PageController, Depends(page_controller)],
) -> Response:
    """Page `n` of the original as a PNG, unrotated, `scale` pixels per point."""
    reply = await controller.page(doc, page=n, scale=scale, build=build)
    return Response(reply.png, media_type="image/png", headers=reply.headers)


async def sweep_forever() -> None:
    """Every minute, delete documents idle past the hour. Runs for the app's life.

    A pass that fails is logged and the next one runs as usual: stopping would
    keep every document on disk from then on.
    """
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            await asyncio.to_thread(store.sweep)
        except Exception:  # one bad pass must not end expiry for good
            _logger.exception("A sweep failed; the next runs in %s s", SWEEP_EVERY_S)
