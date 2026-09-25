"""The real app with its workers running, and browsers to call it with."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from squidpdf.api.app import create_app

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"


def _crashes() -> APIRouter:
    """A route with a bug in it, to see what a crash looks like from outside."""
    router = APIRouter()

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    return router


@pytest.fixture(scope="module")
def app(tmp_path_factory) -> Iterator[FastAPI]:
    """One app per test module, workers started, documents kept in a fresh folder."""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        app = create_app()
        app.include_router(_crashes())
        with TestClient(app):  # runs the lifespan: the pool and the sweeper
            yield app


@pytest.fixture
def browser(app: FastAPI) -> Callable[[], TestClient]:
    """Opens a new browser on the app each call; each keeps its own cookies."""
    return lambda: TestClient(app, base_url=BASE_URL)


def upload(client: TestClient, body: bytes) -> Response:
    """Send a file the way the browser does: the raw bytes, no form."""
    return client.post(
        "/api/documents", content=body, headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def pdf_bytes(pdf: str) -> bytes:
    return Path(pdf).read_bytes()
