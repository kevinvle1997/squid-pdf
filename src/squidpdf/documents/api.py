"""Upload a document, read it, delete it, and view its pages.

Also `load`, the dependency every route on a document starts with: editing
reuses it. Routes stay thin: owner, validate, pool, reply.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Annotated

import orjson
import xxhash
from fastapi import APIRouter, Depends, Query, Request, Response, status

from squidpdf.api import constants as limits
from squidpdf.api import language, owner, pool
from squidpdf.api.language import Language
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, Message, NotFound, Page, words
from squidpdf.core.constants import CONDENSE_LIMIT, SHRINK_FLOOR, TOLERANCE_PT
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse, page_image
from squidpdf.documents.constants import DOCUMENT_CACHE, PAGE_CACHE, SWEEP_EVERY_S
from squidpdf.documents.errors import NoSuchPage, NotAPdf, TooLarge
from squidpdf.documents.types import (
    Analysis,
    Copy,
    Document,
    DocumentNoticeInfo,
    FontFacts,
    FontInfo,
    Loaded,
)

router = APIRouter(prefix="/api/documents")

_logger = logging.getLogger(__name__)

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
    said_in: Language,
    response: Response,
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
        analysis = await workers.run(
            limits.UPLOAD_TIMEOUT_S, analyse, str(folder), limits.MAX_PAGES
        )
    except BaseException:  # refused, damaged, or the browser left: keep nothing
        store.delete(folder)
        raise
    response.headers.update(language.headers(said_in))
    return _document(doc_id, store.touch(folder), analysis, said_in)


@router.get("/{doc_id}", response_model=Document)
async def read(
    doc: Annotated[Loaded, Depends(load)],
    request: Request,
    response: Response,
    workers: Annotated[Pool, Depends(pool.current)],
    said_in: Language,
) -> Document | Response:
    """The document, in the reader's language. Worked out again only for a new `build`."""
    raw = store.load_analysis(doc.folder, BUILD)
    if raw is None:
        analysis = await workers.run(
            limits.UPLOAD_TIMEOUT_S, analyse, str(doc.folder), limits.MAX_PAGES
        )
        raw = orjson.dumps(analysis)
    else:
        analysis = orjson.loads(raw)
    # Over the analysis and the words it's said in; not `expires_at`, which moves
    # on every visit and is a hint. The analysis is in no language, so the words
    # too: another language, or a sentence reworded since, is another body.
    said = orjson.dumps([said_in, words.catalog(said_in)])
    etag = f'"{xxhash.xxh3_64_hexdigest(raw + said)}"'
    headers = {"ETag": etag, "Cache-Control": DOCUMENT_CACHE, **language.headers(said_in)}
    if request.headers.get("if-none-match") == etag:  # absent on a first read
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return _document(doc.id, doc.expires_at, analysis, said_in)


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


def _document(doc_id: str, expires_at: float, analysis: Analysis, said_in: str) -> Document:
    """The analysis, plus what belongs to this document and this moment, in `said_in`."""
    return {
        "build": analysis["build"],
        "pages": analysis["pages"],
        "spans": analysis["spans"],
        "fonts": [_font_info(font, said_in) for font in analysis["fonts"]],
        "id": doc_id,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "fit": {
            "tolerance_pt": TOLERANCE_PT,
            "condense_limit": CONDENSE_LIMIT,
            "shrink_floor": SHRINK_FLOOR,
        },
        "copy": _copy(said_in),
        "notices": _notices(analysis, said_in),
    }


def _font_info(font: FontFacts, said_in: str) -> FontInfo:
    """A font as the browser gets it: why its own copy can't be used, in `said_in`."""
    why = None if font["why"] is None else Message.from_info(font["why"])
    return {
        "name": font["name"],
        "substitute": font["substitute"],
        "why": None if why is None else words.render(why, said_in),
        "why_code": None if why is None else why.key,
        "why_params": {} if why is None else why.params,
        "same_widths": font["same_widths"],
        "glyphs": font["glyphs"],
    }


def _copy(said_in: str) -> Copy:
    """The sentences the browser fills in as the user types, in `said_in`, unfilled."""
    options = {
        name: {part: words.sentence(key, said_in) for part, key in keys.items()}
        for name, keys in words.OPTION_KEYS.items()
    }
    return {
        "missing": words.sentence("missing", said_in),
        "too_long": words.sentence("too_long", said_in),
        "stand_in": words.sentence("stand_in", said_in),
        "stand_in_same_widths": words.sentence("stand_in_same_widths", said_in),
        "undo_redaction": words.sentence("undo_redaction", said_in),
        "options": options,
    }


def _notices(analysis: Analysis, said_in: str) -> list[DocumentNoticeInfo]:
    """What may not be what the user expected of this document, in `said_in`."""
    # A scan has no text layer: say so, rather than show a page nothing on can be edited.
    if analysis["spans"]:
        return []
    no_text = Message("no_text")
    return [{"type": "no_text", "detail": words.render(no_text, said_in), **no_text.as_info()}]
