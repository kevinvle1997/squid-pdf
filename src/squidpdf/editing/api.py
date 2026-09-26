"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

Also the font list: every face we ship that new text can be drawn in.

Routes stay thin: load, validate, pool, reply. The reply is where what went
wrong is put into words: the pool hands back Messages. Imports `documents.api`
for `load`, the one allowed direction; documents never imports editing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import orjson
from fastapi import APIRouter, Body, Depends, Response
from pydantic import Field

from squidpdf.api import constants as limits
from squidpdf.api import pool
from squidpdf.api.errors import InvalidRequest
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD, words
from squidpdf.documents import api as documents
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.types import Loaded
from squidpdf.editing import work
from squidpdf.editing.constants import FONT_LIST_CACHE
from squidpdf.editing.edits import Insert, Redact, Replace
from squidpdf.editing.errors import TextTooLong, TooManyEdits
from squidpdf.editing.fit import FitReport
from squidpdf.editing.fonts import font_list
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

router = APIRouter(prefix="/api/documents")
fonts_router = APIRouter(prefix="/api/fonts")

# Read by `kind` first: one bad kind is one error, not one per edit type.
AnyEdit = Annotated[Replace | Redact | Insert, Field(discriminator="kind")]

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
) -> Render:
    """Each region drawn with the edits on its page, a fit per replace, and what was skipped.

    Keeps nothing. A redaction pointing at nothing fails the whole request.
    """
    if len(edits) > limits.MAX_EDITS:
        raise TooManyEdits(limits.MAX_EDITS)
    too_long = any(
        isinstance(edit, Replace | Insert) and len(edit.text) > limits.MAX_TEXT_CHARS
        for edit in edits
    )
    if too_long:
        raise TextTooLong(limits.MAX_TEXT_CHARS)
    pages = store.load_pages(doc.folder)
    for region in regions:
        if not 0 <= region.page < len(pages):
            raise NoSuchPage(debug=f"regions: no page {region.page}")
        top = 0.0 if region.y0 is None else region.y0
        bottom = pages[region.page].height if region.y1 is None else region.y1
        # `not <` rather than `>=`: every comparison with NaN is false, so this refuses it too.
        if not top < bottom:
            reason = f"regions: y0 must be a number above y1 on page {region.page}"
            raise InvalidRequest(debug=reason)
    scales = {r.page: documents.page_scale(pages[r.page], scale) for r in regions}
    # A redaction pointing at nothing raises BadReference in the worker.
    rendered = await workers.run(
        limits.RENDER_TIMEOUT_S, work.render, str(doc.folder), edits, regions, scales
    )
    fits = rendered.fits
    return {
        "images": rendered.images,
        "fits": {span_id: _fit_info(fit) for span_id, fit in fits.replaces.items()},
        "insert_fits": [
            {**_fit_info(fit), "edit": position} for position, fit in fits.inserts.items()
        ],
        "redactions": [],  # verdicts come with export and the redaction check
        "skipped": [_skipped_info(skipped) for skipped in rendered.skipped],
        "notices": [_notice_info(notice) for notice in rendered.notices],
        "build": BUILD,
        "expires_at": datetime.fromtimestamp(doc.expires_at, UTC).isoformat(),
    }


def _fit_info(fit: FitReport) -> FitInfo:
    """A fit as the browser gets it: option names, since it has their sentences."""
    return {
        "delta_pt": fit.delta_pt,
        "missing": fit.missing,
        "left_out": fit.left_out,
        "options": [o.name for o in fit.options],
        "strategy": fit.strategy,
        "message": words.render_all(fit.describe()),
    }


def _skipped_info(skipped: Skipped) -> SkippedInfo:
    """An edit left out, as the browser gets it: why, in words."""
    return {"edit": skipped.edit, "type": skipped.type, "detail": words.render(skipped.detail)}


def _notice_info(notice: Notice) -> NoticeInfo:
    """An edit drawn other than asked, as the browser gets it: why, in words."""
    detail = words.render(notice.detail)
    return {"span_id": notice.span_id, "detail": detail, "edit": notice.edit}
