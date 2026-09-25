"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from squidpdf.api import errors
from squidpdf.api.pool import Pool


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Workers start with the app and stop with it."""
    app.state.pool = Pool()
    try:
        yield
    finally:
        app.state.pool.close()


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

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Up and answering."""
        return {"status": "ok"}

    return app
