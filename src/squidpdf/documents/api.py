"""Upload a document, read it, delete it, and view its pages.

Routes are HTTP only: read the request, call the action's controller, send
what it hands back. Delete has none: it's one call to the store. Also `load`,
the dependency every route on a document starts with; editing reuses it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits
from squidpdf.api import owner
from squidpdf.api.body import declared_size
from squidpdf.api.controllers import controller, response_of
from squidpdf.api.language import ReaderLanguage
from squidpdf.core import NotFound
from squidpdf.documents import store
from squidpdf.documents.constants import SWEEP_EVERY_S
from squidpdf.documents.pages import PageController
from squidpdf.documents.read import ReadController
from squidpdf.documents.types import Document, Loaded
from squidpdf.documents.upload import UploadController

__all__ = [
    "router",
    "load",
    "upload",
    "read",
    "delete",
    "page",
    "sweep_forever",
]

router = APIRouter(prefix="/api/documents")

_logger = logging.getLogger(__name__)

_JSON = "application/json"


def load(doc_id: str, request: Request) -> Loaded:
    """This browser's document, or not_found for any other: missing, expired or not theirs."""
    found = store.find(doc_id)
    if found is None:
        raise NotFound()
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, store.touch(folder))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Document)
async def upload(
    request: Request,
    *,
    token: Annotated[str, Depends(owner.token)],
    controller: Annotated[UploadController, Depends(controller(UploadController))],
    said_in: ReaderLanguage,
    response: Response,
) -> Document:
    """A raw PDF body, no multipart and no filename. Answers with every span judged."""
    reply = await controller.upload(
        owner.digest(token),
        declared=declared_size(request),
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
    controller: Annotated[ReadController, Depends(controller(ReadController))],
    said_in: ReaderLanguage,
) -> Response:
    """The document, in the reader's language, or 304 if the browser has it already."""
    if_none_match = request.headers.get("if-none-match")  # absent on a first read
    reply = await controller.read(doc, said_in=said_in, if_none_match=if_none_match)
    return response_of(reply, media_type=_JSON)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(doc: Annotated[Loaded, Depends(load)]) -> None:
    """The document and everything worked out from it, now rather than in an hour."""
    store.delete(doc.folder)


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
    controller: Annotated[PageController, Depends(controller(PageController))],
) -> Response:
    """Page `n` of the original as a PNG, unrotated, `scale` pixels per point."""
    reply = await controller.page(doc, page=n, scale=scale, build=build)
    return response_of(reply, media_type="image/png")


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
