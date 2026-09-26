"""Render: the browser's edits drawn on the rows it's showing, with a fit for each.

Routes stay thin: load, validate, pool, reply. Imports `documents.api` for
`load`, the one allowed direction; documents never imports editing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from pydantic import Field

from squidpdf.api import constants as limits
from squidpdf.api import pool
from squidpdf.api.errors import InvalidRequest
from squidpdf.api.pool import Pool
from squidpdf.core import BUILD
from squidpdf.documents import api as documents
from squidpdf.documents import store
from squidpdf.documents.errors import NoSuchPage
from squidpdf.documents.types import Loaded
from squidpdf.editing import work
from squidpdf.editing.edits import Insert, Redact, Replace
from squidpdf.editing.errors import TextTooLong, TooManyEdits
from squidpdf.editing.types import Region, Render

router = APIRouter(prefix="/api/documents")

# Read by `kind` first: one bad kind is one error, not one per edit type.
AnyEdit = Annotated[Replace | Redact | Insert, Field(discriminator="kind")]


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
        isinstance(edit, Replace) and len(edit.text) > limits.MAX_REPLACE_CHARS
        for edit in edits
    )
    if too_long:
        raise TextTooLong(limits.MAX_REPLACE_CHARS)
    pages = store.load_pages(doc.folder)
    for region in regions:
        if not 0 <= region.page < len(pages):
            raise NoSuchPage(debug=f"regions: no page {region.page}")
        top = 0.0 if region.y0 is None else region.y0
        bottom = pages[region.page].height if region.y1 is None else region.y1
        if top >= bottom:
            reason = f"regions: y0 must be above y1 on page {region.page}"
            raise InvalidRequest(debug=reason)
    scales = {r.page: documents.page_scale(pages[r.page], scale) for r in regions}
    # A redaction pointing at nothing raises BadReference in the worker.
    rendered = await workers.run(
        limits.RENDER_TIMEOUT_S, work.render, str(doc.folder), edits, regions, scales
    )
    return {
        **rendered,
        "build": BUILD,
        "expires_at": datetime.fromtimestamp(doc.expires_at, UTC).isoformat(),
    }
