"""The real app, a stand-in for documents, and browsers to call it with."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from squidpdf.api import owner
from squidpdf.api.app import create_app
from squidpdf.api.errors import ApiError

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"


def _stand_in() -> APIRouter:
    """Documents' ownership without documents, until they exist.

    Create hands out an id and keeps the owner's digest, as a document folder
    will; reading checks it, as the load-document dependency will.
    """
    router = APIRouter()
    owners: dict[str, str] = {}

    @router.post("/api/things", status_code=201)
    def create(token: Annotated[str, Depends(owner.token)]) -> dict[str, str]:
        thing = secrets.token_urlsafe(16)
        owners[thing] = owner.digest(token)
        return {"id": thing}

    # `scale` is only there to have something to get wrong.
    @router.get("/api/things/{thing}")
    def read(thing: str, request: Request, scale: int = 1) -> dict[str, str]:
        if thing not in owners:
            raise ApiError("not_found")
        owner.check(request, owners[thing])
        return {"id": thing}

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    return router


@pytest.fixture
def app() -> FastAPI:
    app = create_app()
    app.include_router(_stand_in())
    return app


@pytest.fixture
def browser(app: FastAPI) -> Callable[[], TestClient]:
    """Opens a new browser on the app each call; each keeps its own cookies."""
    return lambda: TestClient(app, base_url=BASE_URL)
