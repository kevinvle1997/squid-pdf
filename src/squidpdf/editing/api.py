"""Render, export and the font list, over HTTP.

Routes are HTTP only: read the request, call the action's controller, send
what it hands back. Imports `documents.api` for `load`, the one allowed
direction; documents never imports editing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from squidpdf.api import constants as limits
from squidpdf.api import pool
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.pool import Pool
from squidpdf.documents import api as documents
from squidpdf.documents.types import Loaded
from squidpdf.editing.edits import Insert, Redact, Replace
from squidpdf.editing.export import ExportController
from squidpdf.editing.fonts import FontListController
from squidpdf.editing.render import RenderController
from squidpdf.editing.types import FontList, Region, Render

__all__ = [
    "router",
    "fonts_router",
    "AnyEdit",
    "RenderBody",
    "ExportBody",
    "render_controller",
    "export_controller",
    "font_list_controller",
    "fonts",
    "render",
    "export",
]

router = APIRouter(prefix="/api/documents")
fonts_router = APIRouter(prefix="/api/fonts")

# Read by `kind` first: one bad kind is one error, not one per edit type.
AnyEdit = Annotated[Replace | Redact | Insert, Field(discriminator="kind")]


class RenderBody(BaseModel):
    """What render reads from the request body."""

    edits: list[AnyEdit]
    scale: Annotated[int, Field(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))]
    regions: list[Region]


class ExportBody(BaseModel):
    """What export reads from the request body. No `pages` means every page."""

    edits: list[AnyEdit]
    pages: list[int] | None = None


def render_controller(workers: Annotated[Pool, Depends(pool.current)]) -> RenderController:
    """Render's controller, on the app's workers."""
    return RenderController(workers)


def export_controller(workers: Annotated[Pool, Depends(pool.current)]) -> ExportController:
    """Export's controller, on the app's workers."""
    return ExportController(workers)


def font_list_controller(workers: Annotated[Pool, Depends(pool.current)]) -> FontListController:
    """The font list's controller, on the app's workers."""
    return FontListController(workers)


@fonts_router.get("", response_model=FontList)
async def fonts(
    build: str, controller: Annotated[FontListController, Depends(font_list_controller)]
) -> Response:
    """Every face new text can be drawn in, by family, with each letter's width."""
    reply = await controller.font_list(build)
    return Response(reply.body, media_type="application/json", headers=reply.headers)


@router.post("/{doc_id}/render", response_model=Render)
async def render(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: RenderBody,
    controller: Annotated[RenderController, Depends(render_controller)],
    said_in: ReaderLanguage,
) -> Response:
    """Each region drawn with the edits on its page, a fit per edit, and what was skipped."""
    reply = await controller.render(
        doc, edits=body.edits, regions=body.regions, scale=body.scale, said_in=said_in
    )
    return JSONResponse(reply.body, headers=reply.headers)


@router.post(
    "/{doc_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: ExportBody,
    controller: Annotated[ExportController, Depends(export_controller)],
    said_in: ReaderLanguage,
) -> Response:
    """The edited document as a PDF: every page, or `pages` in that order."""
    reply = await controller.export(doc, edits=body.edits, pages=body.pages, said_in=said_in)
    return Response(reply.body, media_type="application/pdf", headers=reply.headers)
