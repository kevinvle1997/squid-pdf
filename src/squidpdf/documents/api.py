from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits, owner, rate
from squidpdf.api.body import declared_size
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.routing import controller_with_workers, listed_header, response_of
from squidpdf.core import LogController, LogEvent, NotFound
from squidpdf.documents import constants
from squidpdf.documents.errors import NotSentAsPdf
from squidpdf.documents.fonts import AttachController, DetachController
from squidpdf.documents.meters import Meter, Meters
from squidpdf.documents.page_image import PageController
from squidpdf.documents.read import EXPIRES_HEADER, ReadController
from squidpdf.documents.store import DocumentStore
from squidpdf.documents.types import Document, Loaded
from squidpdf.documents.upload import UploadController

router = APIRouter(prefix="/api/documents")

_log = LogController.for_module(__name__)

_JSON = "application/json"
_PDF = "application/pdf"


def _documents_of(request: Request) -> DocumentStore:
    return request.app.state.documents


def _meter_of(request: Request) -> Meter:
    """A plain def, so FastAPI runs it in a thread: a meter may read a table."""
    meters: Meters = request.app.state.meters
    return meters.meter_for(request.cookies)


def load(doc_id: str, request: Request) -> Loaded:
    documents = _documents_of(request)
    found = documents.find(doc_id)
    if found is None:
        raise NotFound()
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, documents.touch(folder))


async def _admit_pdf_only(request: Request) -> None:
    """Another site can send a form without asking first: counted, it would use up uploads."""
    sent_as = request.headers.get("content-type")
    media_type = (sent_as or "").partition(";")[0].strip().lower()
    if media_type != _PDF:
        raise NotSentAsPdf(debug=f"sent as {sent_as!r}")


# Under way before it counts for the minute, so one refused as too many at once spends none.
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=Document,
    dependencies=[
        Depends(_admit_pdf_only),
        Depends(rate.upload_turn),
        Depends(rate.admit_upload),
    ],
)
async def upload(
    request: Request,
    *,
    token: Annotated[str, Depends(owner.token)],
    meter: Annotated[Meter, Depends(_meter_of)],
    upload_controller: Annotated[
        UploadController, Depends(controller_with_workers(UploadController))
    ],
    language: ReaderLanguage,
    response: Response,
) -> Response:
    reply = await upload_controller.upload(
        owner.digest(token),
        meter=meter,
        declared=declared_size(request),
        chunks=request.stream(),
        language=language,
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
    language: ReaderLanguage,
) -> Response:
    if_none_match = request.headers.get("if-none-match")
    reply = await read_controller.read(doc, language=language, if_none_match=if_none_match)
    return response_of(reply, media_type=_JSON)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    doc: Annotated[Loaded, Depends(load)],
    documents: Annotated[DocumentStore, Depends(_documents_of)],
) -> None:
    documents.delete(doc.folder)


@router.put(
    "/{doc_id}/fonts/{font_name:path}",
    response_model=Document,
    dependencies=[Depends(rate.upload_turn)],
)
async def attach_font(
    doc: Annotated[Loaded, Depends(load)],
    *,
    font_name: str,
    request: Request,
    attach_controller: Annotated[
        AttachController, Depends(controller_with_workers(AttachController))
    ],
    language: ReaderLanguage,
) -> Response:
    reply = await attach_controller.attach(
        doc,
        font_name=font_name,
        declared=declared_size(request),
        chunks=request.stream(),
        language=language,
    )
    return response_of(reply, media_type=_JSON)


@router.delete("/{doc_id}/fonts/{font_name:path}", response_model=Document)
async def detach_font(
    doc: Annotated[Loaded, Depends(load)],
    *,
    font_name: str,
    detach_controller: Annotated[
        DetachController, Depends(controller_with_workers(DetachController))
    ],
    language: ReaderLanguage,
) -> Response:
    reply = await detach_controller.detach(doc, font_name=font_name, language=language)
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
    reply = await page_controller.page(doc, page=n, scale=scale, build=build)
    return response_of(reply, media_type="image/png")


async def sweep_forever(documents: DocumentStore) -> None:
    """A failed pass is logged and the next runs: stopping would keep every document on disk."""
    while True:
        await asyncio.sleep(constants.SWEEP_EVERY_S)
        try:
            await asyncio.to_thread(documents.sweep)
        except Exception as failure:  # noqa: BLE001 (one bad pass must not end expiry)
            _log.write(LogEvent.SWEEP_FAILED, failure, next_in_s=constants.SWEEP_EVERY_S)
