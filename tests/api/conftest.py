"""The real app, a stand-in for documents, and browsers to call it with."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.api.errors import Problem

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"


def _stand_in() -> APIRouter:
    """Routes to get wrong, until documents exist."""
    router = APIRouter()

    # `scale` is only there to have something to get wrong.
    @router.get("/api/things/{thing}")
    def read(thing: str, scale: int = 1) -> dict[str, str]:
        raise Problem("not_found")

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
