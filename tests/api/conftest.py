"""The real app with its workers running, one for the whole run, and browsers to call it."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from squidpdf.api import constants as api_constants
from squidpdf.api.app import create_app

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"
_MARGIN_PT = 4  # above and below a line, as the browser pads its strip


def _crashes() -> APIRouter:
    """A route with a bug in it, to see what a crash looks like from outside."""
    router = APIRouter()

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    return router


# Every test uploads from one address, far more often than a browser may: plenty for the suite.
_SUITE_UPLOADS_PER_MINUTE = 10_000


@pytest.fixture(scope="session")
def session_app(tmp_path_factory) -> Iterator[FastAPI]:
    """One app for the whole run, workers started once: a pool's first task costs a second.

    Under xdist each worker is its own session, so each has one. A test that kills a
    worker or changes the app for good builds its own pool or app, never this one.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        # The per-address upload limit reads this when each upload comes in.
        env.setattr(api_constants, "UPLOADS_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        app = create_app()
        app.include_router(_crashes())
        yield app


@pytest.fixture(scope="session")
def server(session_app: FastAPI) -> Iterator[TestClient]:
    """The run's app started, its pool and sweeper with it, in the loop every browser shares."""
    with TestClient(session_app) as server:  # runs the lifespan
        yield server


@pytest.fixture(scope="module")
def app(session_app: FastAPI, server: TestClient, tmp_path_factory) -> Iterator[FastAPI]:
    """The run's app, started, with this module's documents kept in a fresh folder of their own.

    The folder is read on each request, and work goes to the pool by absolute path,
    so the workers started for an earlier module find this one's documents.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        yield session_app


def browser_on(server: TestClient, **options: Any) -> TestClient:
    """A new browser on `server`'s app, with cookies of its own; `options` go to TestClient.

    Its requests run in the loop `server` started the app in, as a server runs
    one: the pool works only in the loop it was made in, and a TestClient on its
    own gives each request a new loop.
    """
    browser = TestClient(server.app, base_url=BASE_URL, **options)
    browser.portal = server.portal
    return browser


@pytest.fixture
def browser(app: FastAPI, server: TestClient) -> Callable[[], TestClient]:
    """Opens a new browser on the app each call; each keeps its own cookies."""
    return lambda: browser_on(server)


def upload(client: TestClient, body: bytes) -> Response:
    """Send a file the way the browser does: the raw bytes, no form."""
    return client.post(
        "/api/documents", content=body, headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def pdf_bytes(pdf: str) -> bytes:
    """The shared sample PDF, as a browser would upload it."""
    return Path(pdf).read_bytes()


@pytest.fixture
def mine(browser: Callable[[], TestClient]) -> TestClient:
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine: TestClient, pdf_bytes: bytes) -> dict:
    """The sample PDF as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


def span_starting(doc: dict, page: int, starts: str) -> dict:
    """The span on `page` of an uploaded `doc` whose text starts with `starts`."""
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def around(span: dict) -> dict:
    """A region: the full-width strip over a span's line."""
    box = span["bbox"]
    return {"page": span["page"], "y0": box["y0"] - _MARGIN_PT, "y1": box["y1"] + _MARGIN_PT}
