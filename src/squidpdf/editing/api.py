"""Render, export and the font list, over HTTP.

Routes are HTTP only: read the request, call the action's controller, send
what it hands back. Imports `documents.api` for `load`, the one allowed
direction; documents never imports editing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from squidpdf.api import constants as limits
from squidpdf.api.controllers import controller, response_of
from squidpdf.api.language import ReaderLanguage
from squidpdf.documents import api as documents
from squidpdf.documents.types import Loaded
from squidpdf.editing.edits import Edit
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
    "fonts",
    "render",
    "export",
]

router = APIRouter(prefix="/api/documents")
fonts_router = APIRouter(prefix="/api/fonts")

# Read by `kind` first: one bad kind is one error, not one per edit type. The union is
# edits.py's, so a new edit type is read here as soon as it's added there.
AnyEdit = Annotated[Edit, Field(discriminator="kind")]


class RenderBody(BaseModel):
    """What render reads from the request body."""

    edits: list[AnyEdit]
    scale: Annotated[int, Field(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))]
    regions: list[Region]


class ExportBody(BaseModel):
    """What export reads from the request body. No `pages` means every page."""

    edits: list[AnyEdit]
    pages: list[int] | None = None


@fonts_router.get("", response_model=FontList)
async def fonts(
    build: str,
    controller: Annotated[FontListController, Depends(controller(FontListController))],
) -> Response:
    """Every face new text can be drawn in, by family, with each letter's width."""
    reply = await controller.font_list(build)
    return response_of(reply, media_type="application/json")


@router.post("/{doc_id}/render", response_model=Render)
async def render(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: RenderBody,
    controller: Annotated[RenderController, Depends(controller(RenderController))],
    said_in: ReaderLanguage,
) -> Response:
    """Each region drawn with the edits on its page, a fit per edit, and what was skipped."""
    reply = await controller.render(
        doc, edits=body.edits, regions=body.regions, scale=body.scale, said_in=said_in
    )
    return response_of(reply, media_type="application/json")


@router.post(
    "/{doc_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def export(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: ExportBody,
    controller: Annotated[ExportController, Depends(controller(ExportController))],
    said_in: ReaderLanguage,
) -> Response:
    """The edited document as a PDF: every page, or `pages` in that order."""
    reply = await controller.export(doc, edits=body.edits, pages=body.pages, said_in=said_in)
    return response_of(reply, media_type="application/pdf")
