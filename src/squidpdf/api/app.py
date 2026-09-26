"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from squidpdf.api import constants as limits
from squidpdf.api import errors
from squidpdf.api.pool import Pool
from squidpdf.documents import api as documents
from squidpdf.editing import api as editing


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Workers and the expiry sweeper start with the app and stop with it."""
    app.state.pool = Pool()
    sweeper = asyncio.create_task(documents.sweep_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        app.state.pool.close()


async def _limit_body(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Refuse a too-large edit list before it's read. Uploads check their own, larger limit.

    Only a body that says its size up front; a chunked one is read regardless.
    """
    declared = request.headers.get("content-length")  # absent when the body is chunked
    is_upload = request.method == "POST" and request.url.path == "/api/documents"
    too_large = declared is not None and not is_upload and int(declared) > limits.MAX_BODY_BYTES
    if too_large:
        return errors.response(errors.RequestTooLarge())
    return await call_next(request)


def create_app() -> FastAPI:
    """A fresh app, so each test gets its own."""
    app = FastAPI(
        title="squid-pdf",
        lifespan=_lifespan,
        # Everything lives under /api; the rest of the host is the frontend's.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    errors.install(app)
    app.middleware("http")(_limit_body)
    app.include_router(documents.router)
    app.include_router(editing.router)
    app.include_router(editing.fonts_router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Up and answering."""
        return {"status": "ok"}

    return app
