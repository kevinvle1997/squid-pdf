from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from squidpdf.terms.types import Terms

router = APIRouter(prefix="/api/terms")


def _terms_of(request: Request) -> Terms | None:
    return request.app.state.terms


@router.get("")
async def terms(deployed: Annotated[Terms | None, Depends(_terms_of)]) -> Terms | None:
    return deployed
