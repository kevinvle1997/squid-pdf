"""Imports `documents.api` for `load`, the one allowed direction: never the other way."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from squidpdf.api import constants as limits
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.routing import (
    controller_with_app_workers,
    controller_with_workers,
    listed_header,
    response_of,
)
from squidpdf.core import Workers
from squidpdf.documents import api as documents
from squidpdf.documents.types import Loaded
from squidpdf.editing.edits import Edit
from squidpdf.editing.export import NOTICES_HEADER, SKIPPED_HEADER, ExportController
from squidpdf.editing.font_list import FontListController
from squidpdf.editing.render import RenderController
from squidpdf.editing.types import FontList, Region, Render

router = APIRouter(prefix="/api/documents")
fonts_router = APIRouter(prefix="/api/fonts")

# Read by `kind` first: one bad kind is one error, not one per edit type.
_AnyEdit = Annotated[Edit, Field(discriminator="kind")]


class RenderBody(BaseModel):
    edits: list[_AnyEdit]
    scale: Annotated[int, Field(ge=min(limits.PAGE_SCALES), le=max(limits.PAGE_SCALES))]
    regions: list[Region]


class ExportBody(BaseModel):
    edits: list[_AnyEdit]
    pages: list[int] | None = None


def measure_font_list_ahead(workers: Workers) -> None:
    FontListController(workers).measure_ahead()


@fonts_router.get("", response_model=FontList)
async def fonts(
    build: str,
    font_list_controller: Annotated[
        FontListController, Depends(controller_with_app_workers(FontListController))
    ],
) -> Response:
    reply = await font_list_controller.font_list(build)
    return response_of(reply, media_type="application/json")


@router.post("/{doc_id}/render", response_model=Render)
async def render(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: RenderBody,
    render_controller: Annotated[
        RenderController, Depends(controller_with_workers(RenderController))
    ],
    language: ReaderLanguage,
) -> Response:
    reply = await render_controller.render(
        doc, edits=body.edits, regions=body.regions, scale=body.scale, language=language
    )
    return response_of(reply, media_type="application/json")


@router.post(
    "/{doc_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "headers": {
                SKIPPED_HEADER: listed_header(
                    "Each edit left out, by its place in the list sent, joined by commas"
                ),
                NOTICES_HEADER: listed_header(
                    "What saving did to the whole file, as a JSON list of notices"
                ),
            },
        }
    },
)
async def export(
    doc: Annotated[Loaded, Depends(documents.load)],
    *,
    body: ExportBody,
    export_controller: Annotated[
        ExportController, Depends(controller_with_workers(ExportController))
    ],
    language: ReaderLanguage,
) -> Response:
    reply = await export_controller.export(
        doc, edits=body.edits, pages=body.pages, language=language
    )
    return response_of(reply, media_type="application/pdf")
