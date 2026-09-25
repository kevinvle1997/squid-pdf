"""Upload a document, read it, delete it, and view its pages.

Also `load`, the dependency every route on a document starts with: editing
reuses it. Routes stay thin: owner, validate, pool, reply.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, TypedDict

import orjson
import xxhash
from fastapi import APIRouter, Depends, Query, Request, Response

from squidpdf.api import limits, owner, pool
from squidpdf.api.errors import ApiError, Problem
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, words
from squidpdf.documents import store
from squidpdf.documents.analyse import Analysis, analyse, page_image
from squidpdf.editing.fit import CONDENSE_LIMIT, TOLERANCE_PT

router = APIRouter(prefix="/api/documents")

_PDF_HEADER = b"%PDF-"
_HEADER_WINDOW = 1024  # readers accept the header anywhere in the first KB
# The URL carries `build`, so its bytes never change; an hour matches the idle expiry.
_PAGE_CACHE = f"private, max-age={store.IDLE_S}, immutable"
_DOCUMENT_CACHE = "private, no-cache"


class FitRules(TypedDict):
    """The thresholds the browser runs the fit check with, the server's own."""

    tolerance_pt: float
    condense_limit: float


class Copy(TypedDict):
    """The sentences the browser fills in as the user types."""

    missing: str
    too_long: str
    options: dict[str, dict[str, str]]


class Document(Analysis):
    """The stored document, as the browser gets it."""

    id: str
    expires_at: str
    fit: FitRules
    copy: Copy
    notices: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class Loaded:
    """A document this browser owns, with its hour just restarted."""

    id: str
    folder: Path
    expires_at: float


def load(doc_id: str, request: Request) -> Loaded:
    """This browser's document, or not_found for any other: missing, expired or not theirs."""
    found = store.find(doc_id)
    if found is None:
        raise ApiError(Problem.NOT_FOUND)
    folder, digest = found
    owner.check(request, digest)
    return Loaded(doc_id, folder, store.touch(folder))


@router.post("", status_code=201, response_model=Document)
async def upload(
    request: Request,
    token: Annotated[str, Depends(owner.token)],
    workers: Annotated[Pool, Depends(pool.current)],
) -> Document:
    """A raw PDF body, no multipart and no filename. Answers with every span judged."""
    doc_id, folder = store.create(owner.digest(token))
    try:
        await _receive(request, folder / store.ORIGINAL)
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
    headers = {"ETag": etag, "Cache-Control": _DOCUMENT_CACHE}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return _document(doc.id, doc.expires_at, analysis)


@router.delete("/{doc_id}", status_code=204)
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
        raise ApiError(Problem.NOT_FOUND)
    width, height = pages[n].width, pages[n].height
    # Less a pixel a side: MuPDF rounds each side up, which could tip it over.
    largest = math.sqrt(limits.MAX_IMAGE_PIXELS / (width * height)) - 1 / min(width, height)
    png = await workers.run(
        limits.RENDER_TIMEOUT_S, page_image, str(doc.folder), n, min(scale, largest)
    )
    cache = _PAGE_CACHE if build == BUILD else "no-store"
    return Response(png, media_type="image/png", headers={"Cache-Control": cache})


async def sweep_forever() -> None:
    """Every minute, delete documents idle past the hour. Runs for the app's life."""
    while True:
        await asyncio.sleep(store.SWEEP_EVERY_S)
        await asyncio.to_thread(store.sweep)


async def _receive(request: Request, dest: Path) -> None:
    """Stream the body to disk, refusing it as soon as it's too big or plainly not a PDF."""
    declared = request.headers.get("content-length")  # absent when the body is chunked
    if declared is not None and int(declared) > limits.MAX_FILE_BYTES:
        raise ApiError(Problem.TOO_LARGE, mb=limits.MAX_FILE_MB)
    size, head = 0, b""
    with dest.open("wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limits.MAX_FILE_BYTES:
                raise ApiError(Problem.TOO_LARGE, mb=limits.MAX_FILE_MB)
            if len(head) < _HEADER_WINDOW:
                head += chunk[: _HEADER_WINDOW - len(head)]
                if len(head) == _HEADER_WINDOW and _PDF_HEADER not in head:
                    raise ApiError(Problem.NOT_A_PDF)
            out.write(chunk)
    if _PDF_HEADER not in head:
        raise ApiError(Problem.NOT_A_PDF)


def _document(doc_id: str, expires_at: float, analysis: Analysis) -> Document:
    """The analysis, plus what belongs to this document and this moment."""
    return {
        **analysis,
        "id": doc_id,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "fit": {"tolerance_pt": TOLERANCE_PT, "condense_limit": CONDENSE_LIMIT},
        "copy": {
            "missing": words.MISSING,
            "too_long": words.TOO_LONG,
            "options": words.OPTIONS,
        },
        "notices": [],  # signed, scanned: once upload checks for them
    }
