"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

Export: the same edits applied to the whole document, and the file sent back.
Also the font list: every face we ship that new text can be drawn in.

Routes are HTTP only: read the request, call the action's controller, shape
the reply. The reply is where what the controller hands back, Messages, is put
into the reader's words. Imports `documents.api` for `load`, the one allowed
direction; documents never imports editing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import orjson
from fastapi import APIRouter, Body, Depends, Response
from pydantic import Field

from squidpdf.api import constants as limits
from squidpdf.api import pool
from squidpdf.api.language import ReaderLanguage, language_headers
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, words
from squidpdf.documents import api as documents
from squidpdf.documents.types import Loaded
from squidpdf.editing.constants import FONT_LIST_CACHE
from squidpdf.editing.edits import Insert, Redact, Replace
from squidpdf.editing.export import ExportController
from squidpdf.editing.fit import FitReport
from squidpdf.editing.fonts import font_list
from squidpdf.editing.render import RenderController
from squidpdf.editing.types import (
    FitInfo,
    FontList,
    Notice,
    NoticeInfo,
    Region,
    Render,
    Skipped,
    SkippedInfo,
)

__all__ = [
    "router",
    "fonts_router",
    "AnyEdit",
    "fonts",
    "render",
    "export",
]

router = APIRouter(prefix="/api/documents")
fonts_router = APIRouter(prefix="/api/fonts")

# Read by `kind` first: one bad kind is one error, not one per edit type.
AnyEdit = Annotated[Replace | Redact | Insert, Field(discriminator="kind")]

# Export's body is the file, so the edits it left out are named here, by place in the list.
_SKIPPED_HEADER = "Squid-Skipped-Edits"

# The font list, worked out once per server under this build: the same for everyone.
# Measured: 5 s of pool work for 700 KB of JSON, so it's kept rather than redone.
_font_lists: dict[str, bytes] = {}


@fonts_router.get("", response_model=FontList)
async def fonts(build: str, workers: Annotated[Pool, Depends(pool.current)]) -> Response:
    """Every face new text can be drawn in, by family, with each letter's width.

    An old `build` still gets the list, but not to keep.
    """
    body = _font_lists.get(BUILD)  # None until the first ask since the server started
    if body is None:
        listed = await workers.run(limits.FONT_LIST_TIMEOUT_S, font_list)
        body = _font_lists[BUILD] = orjson.dumps(listed)
    cache = FONT_LIST_CACHE if build == BUILD else "no-store"
    return Response(body, media_type="application/json", headers={"Cache-Control": cache})


@router.post("/{doc_id}/render", response_model=Render)
async def render(
    doc: Annotated[Loaded, Depends(documents.load)],
    edits: Annotated[list[AnyEdit], Body()],
    scale: Annotated[int, Body(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))],
    regions: Annotated[list[Region], Body()],
    workers: Annotated[Pool, Depends(pool.current)],
    said_in: ReaderLanguage,
    response: Response,
) -> Render:
    """Each region drawn with the edits on its page, a fit per replace, and what was skipped.

    RenderController checks the request and draws; this says it in the reader's words.
    """
    rendered = await RenderController(workers).render(doc.folder, edits, regions, scale)
    response.headers.update(language_headers(said_in))
    fits = rendered.fits
    return {
        "images": rendered.images,
        "fits": {span_id: fit_info(fit, said_in) for span_id, fit in fits.replaces.items()},
        "insert_fits": [
            {**fit_info(fit, said_in), "edit": position}
            for position, fit in fits.inserts.items()
        ],
        "redactions": [],  # verdicts come with export and the redaction check
        "skipped": [skipped_info(skipped, said_in) for skipped in rendered.skipped],
        "notices": [notice_info(notice, said_in) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": datetime.fromtimestamp(doc.expires_at, UTC).isoformat(),
    }


@router.post(
    "/{doc_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export(
    doc: Annotated[Loaded, Depends(documents.load)],
    edits: Annotated[list[AnyEdit], Body()],
    workers: Annotated[Pool, Depends(pool.current)],
    pages: Annotated[list[int] | None, Body()] = None,
) -> Response:
    """The document with the edits applied, as a PDF: every page, or `pages` in that order.

    ExportController checks the request, makes the file and checks its
    redactions; this sends it, the edits left out in a header.
    """
    exported = await ExportController(workers).export(doc.folder, edits, pages)
    skipped = ", ".join(str(position) for position in exported.skipped)
    return Response(
        exported.pdf, media_type="application/pdf", headers={_SKIPPED_HEADER: skipped}
    )


def fit_info(fit: FitReport, said_in: str) -> FitInfo:
    """A fit as the browser gets it: option names, since it has their sentences."""
    parts = fit.describe()
    return {
        "delta_pt": fit.delta_pt,
        "missing": fit.missing,
        "left_out": fit.left_out,
        "options": [option.name for option in fit.options],
        "strategy": fit.strategy,
        "message": words.render_all(parts, said_in),
        "message_parts": [part.as_info() for part in parts],
    }


def skipped_info(skipped: Skipped, said_in: str) -> SkippedInfo:
    """An edit left out, as the browser gets it: why, in words and unsaid."""
    return {
        "edit": skipped.edit,
        "type": skipped.type,
        "detail": words.render(skipped.detail, said_in),
        **skipped.detail.as_info(),
    }


def notice_info(notice: Notice, said_in: str) -> NoticeInfo:
    """An edit drawn other than asked, as the browser gets it: why, in words and unsaid."""
    return {
        "span_id": notice.span_id,
        "detail": words.render(notice.detail, said_in),
        "edit": notice.edit,
        **notice.detail.as_info(),
    }
