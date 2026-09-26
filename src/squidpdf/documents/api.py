"""Upload a document, read it, delete it, and view its pages.

Also `load`, the dependency every route on a document starts with: editing
reuses it. Routes stay thin: owner, validate, pool, reply.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Annotated

import orjson
import xxhash
from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits
from squidpdf.api import owner, pool
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, NotFound, Page, words
from squidpdf.core.constants import CONDENSE_LIMIT, SHRINK_FLOOR, TOLERANCE_PT
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse, page_image
from squidpdf.documents.constants import DOCUMENT_CACHE, PAGE_CACHE, SWEEP_EVERY_S
from squidpdf.documents.errors import NoSuchPage, NotAPdf, TooLarge
from squidpdf.documents.types import Analysis, Document, Loaded

router = APIRouter(prefix="/api/documents")

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB


def load(doc_id: str, request: Request) -> Loaded:
    """This browser's document, or not_found for any other: missing, expired or not theirs."""
    found = store.find(doc_id)
    if found is None:
        raise NotFound()
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, store.touch(folder))


def page_scale(page: Page, scale: int) -> float:
    """`scale`, or the largest that keeps this page under the pixel limit.

    Render's strips use it too, so they line up with the page image.
    """
    width, height = page.width, page.height
    # Less a pixel a side: MuPDF rounds each side up, which could tip it over.
    largest = math.sqrt(limits.MAX_IMAGE_PIXELS / (width * height)) - 1 / min(width, height)
    return min(scale, largest)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Document)
async def upload(
    request: Request,
    token: Annotated[str, Depends(owner.token)],
    workers: Annotated[Pool, Depends(pool.current)],
) -> Document:
    """A raw PDF body, no multipart and no filename. Answers with every span judged."""
    declared = request.headers.get("content-length")  # absent when the body is chunked
    declared_too_large = declared is not None and int(declared) > limits.MAX_FILE_BYTES
    if declared_too_large:
        raise TooLarge(limits.MAX_FILE_MB)
    doc_id, folder = store.create(owner.digest(token))
    try:
        # Streamed to disk, refused as soon as it's too big or plainly not a PDF.
        size, first_kb = 0, b""
        with (folder / store.ORIGINAL).open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limits.MAX_FILE_BYTES:
                    raise TooLarge(limits.MAX_FILE_MB)
                first_kb += chunk[: _HEADER_WINDOW - len(first_kb)]
                header_missing = len(first_kb) == _HEADER_WINDOW and _PDF_HEADER not in first_kb
                if header_missing:
                    raise NotAPdf()
                out.write(chunk)
        if _PDF_HEADER not in first_kb:
            raise NotAPdf()
        analysis = await workers.run(limits.UPLOAD_TIMEOUT_S, analyse, str(folder))
    except BaseException:  # refused, damaged, or the browser left: keep nothing
        store.delete(folder)
        raise
    return _document(doc_id, store.touch(folder), analysis)


@router.get("/{doc_id}", response_model=Document)
async def read(
    doc: Annotated[Loaded, Depends(load)],
    request: Request,
    response: Response,
    workers: Annotated[Pool, Depends(pool.current)],
) -> Document | Response:
    """The document. Worked out again only when `build` has changed since."""
    raw = store.load_analysis(doc.folder, BUILD)
    if raw is None:
        analysis = await workers.run(limits.UPLOAD_TIMEOUT_S, analyse, str(doc.folder))
        raw = orjson.dumps(analysis)
    else:
        analysis = orjson.loads(raw)
    # Over the analysis only: `expires_at` moves on every visit and is a hint.
    etag = f'"{xxhash.xxh3_64_hexdigest(raw)}"'
    headers = {"ETag": etag, "Cache-Control": DOCUMENT_CACHE}
    if request.headers.get("if-none-match") == etag:  # absent on a first read
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return _document(doc.id, doc.expires_at, analysis)


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
    n: int,
    scale: Annotated[int, Query(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))],
    build: str,
    workers: Annotated[Pool, Depends(pool.current)],
) -> Response:
    """Page `n` of the original, unrotated, `scale` pixels per point.

    A page too big for that scale gets the largest that stays under the pixel
    limit. An old `build` still gets the image, but not to keep.
    """
    pages = store.load_pages(doc.folder)
    if not 0 <= n < len(pages):
        raise NoSuchPage()
    png = await workers.run(
        limits.RENDER_TIMEOUT_S, page_image, str(doc.folder), n, page_scale(pages[n], scale)
    )
    cache = PAGE_CACHE if build == BUILD else "no-store"
    return Response(png, media_type="image/png", headers={"Cache-Control": cache})


async def sweep_forever() -> None:
    """Every minute, delete documents idle past the hour. Runs for the app's life."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        await asyncio.to_thread(store.sweep)


def _document(doc_id: str, expires_at: float, analysis: Analysis) -> Document:
    """The analysis, plus what belongs to this document and this moment."""
    return {
        **analysis,
        "id": doc_id,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "fit": {
            "tolerance_pt": TOLERANCE_PT,
            "condense_limit": CONDENSE_LIMIT,
            "shrink_floor": SHRINK_FLOOR,
        },
        "copy": {
            "missing": words.MISSING,
            "too_long": words.TOO_LONG,
            "stand_in": words.STAND_IN,
            "undo_redaction": words.UNDO_REDACTION,
            "options": words.OPTIONS,
        },
        # A scan has no text layer: say so, rather than show a page nothing on can be edited.
        "notices": [] if analysis["spans"] else [{"type": "no_text", "detail": words.NO_TEXT}],
    }
