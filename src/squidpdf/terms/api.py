"""The service's terms, over HTTP.

Read once as the app starts (`deployed_terms`), so the route only hands on what was read: no
controller and no worker, as there's no job to do and nothing to put into the reader's words.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from squidpdf.terms.types import Terms

router = APIRouter(prefix="/api/terms")


def terms_of(request: Request) -> Terms | None:
    """The terms the app read as it started; None where its deployment named none."""
    return request.app.state.terms


@router.get("")
async def terms(deployed: Annotated[Terms | None, Depends(terms_of)]) -> Terms | None:
    """The service's terms, as its deployment wrote them; null where it wrote none."""
    return deployed
