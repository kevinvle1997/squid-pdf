"""Upload a document, read it, delete it, and view its pages.

Routes are HTTP only: read the request, call the action's controller, send
what it hands back. Delete has none: it's one call to the store. Also `load`,
the dependency every route on a document starts with; editing reuses it.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits, owner, rate
from squidpdf.api.body import declared_size
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.routing import controller_with_workers, listed_header, response_of
from squidpdf.core import LogController, LogEvent, NotFound
from squidpdf.documents import store
from squidpdf.documents.constants import SWEEP_EVERY_S
from squidpdf.documents.errors import NotSentAsPdf
from squidpdf.documents.fonts import AttachController, DetachController
from squidpdf.documents.page_image import PageController
from squidpdf.documents.read import EXPIRES_HEADER, ReadController
from squidpdf.documents.types import Document, Loaded
from squidpdf.documents.upload import UploadController

router = APIRouter(prefix="/api/documents")

_log = LogController.for_module(__name__)

_JSON = "application/json"
_PDF = "application/pdf"  # the type the browser sends an upload as


def load(doc_id: str, request: Request) -> Loaded:
    """This browser's document, or not_found for any other: missing, expired or not theirs."""
    found = store.find(doc_id)
    if found is None:
        raise NotFound()
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, store.touch(folder))


async def _admit_pdf_only(request: Request) -> None:
    """Refuse an upload not sent as application/pdf, before it counts or any of it is read.

    Another site's page can send a form, plain text or no type without asking first, with
    this site's password: counted, it would use up the user's uploads for the minute.
    """
    sent_as = request.headers.get("content-type")  # None if the request names no type
    media_type = (sent_as or "").partition(";")[0].strip().lower()
    if media_type != _PDF:
        raise NotSentAsPdf(debug=f"sent as {sent_as!r}")


# Its type checked, then counted, before anything else: neither reads any of the body.
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Document,
    dependencies=[Depends(_admit_pdf_only), Depends(rate.admit_upload)],
)
async def upload(
    request: Request,
    *,
    token: Annotated[str, Depends(owner.token)],
    upload_controller: Annotated[
        UploadController, Depends(controller_with_workers(UploadController))
    ],
    said_in: ReaderLanguage,
    response: Response,
) -> Response:
    """A raw PDF sent as application/pdf, no filename. Answers with every span judged."""
    reply = await upload_controller.upload(
        owner.digest(token),
        declared=declared_size(request),
        chunks=request.stream(),
        said_in=said_in,
    )
    return response_of(reply, media_type=_JSON, set_by=response)


@router.get(
    "/{doc_id}",
    response_model=Document,
    responses={
        200: {
            "headers": {
                EXPIRES_HEADER: listed_header(
                    "When the document now expires, in ISO 8601 and UTC; a 304 says it too"
                )
            }
        }
    },
)
async def read(
    doc: Annotated[Loaded, Depends(load)],
    *,
    request: Request,
    read_controller: Annotated[
        ReadController, Depends(controller_with_workers(ReadController))
    ],
    said_in: ReaderLanguage,
) -> Response:
    """The document, in the reader's language, or 304 if the browser has it already."""
    if_none_match = request.headers.get("if-none-match")  # absent on a first read
    reply = await read_controller.read(doc, said_in=said_in, if_none_match=if_none_match)
    return response_of(reply, media_type=_JSON)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(doc: Annotated[Loaded, Depends(load)]) -> None:
    """The document and everything worked out from it, now rather than in an hour."""
    store.delete(doc.folder)


@router.put("/{doc_id}/fonts/{font_name}", response_model=Document)
async def attach_font(
    doc: Annotated[Loaded, Depends(load)],
    *,
    font_name: str,
    request: Request,
    attach_controller: Annotated[
        AttachController, Depends(controller_with_workers(AttachController))
    ],
    said_in: ReaderLanguage,
) -> Response:
    """The user's own copy of a document font, raw TrueType or OpenType; spans judged again."""
    reply = await attach_controller.attach(
        doc,
        font_name=font_name,
        declared=declared_size(request),
        chunks=request.stream(),
        said_in=said_in,
    )
    return response_of(reply, media_type=_JSON)


@router.delete("/{doc_id}/fonts/{font_name}", response_model=Document)
async def detach_font(
    doc: Annotated[Loaded, Depends(load)],
    *,
    font_name: str,
    detach_controller: Annotated[
        DetachController, Depends(controller_with_workers(DetachController))
    ],
    said_in: ReaderLanguage,
) -> Response:
    """The user's copy of a document font removed; every span judged again."""
    reply = await detach_controller.detach(doc, font_name=font_name, said_in=said_in)
    return response_of(reply, media_type=_JSON)


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
    page_controller: Annotated[
        PageController, Depends(controller_with_workers(PageController))
    ],
) -> Response:
    """Page `n` of the original as a PNG, unrotated, `scale` pixels per point."""
    reply = await page_controller.page(doc, page=n, scale=scale, build=build)
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
        except Exception as failure:  # noqa: BLE001 (one bad pass must not end expiry)
            _log.write(LogEvent.SWEEP_FAILED, failure, next_in_s=SWEEP_EVERY_S)
