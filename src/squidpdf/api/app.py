"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from squidpdf.api import errors
from squidpdf.api.body import BodyLimit
from squidpdf.api.pool import Pool
from squidpdf.documents import api as documents
from squidpdf.editing import api as editing

__all__ = [
    "create_app",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Workers and the expiry sweeper start with the app and stop with it."""
    app.state.pool = Pool()
    sweeper = asyncio.create_task(documents.sweep_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        app.state.pool.close()


def create_app() -> FastAPI:
    """A fresh app, so each test gets its own."""
    app = FastAPI(
        title="squid-pdf",
        lifespan=lifespan,
        # Everything lives under /api; the rest of the host is the frontend's.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    errors.install(app)
    app.include_router(documents.router)
    app.include_router(editing.router)
    app.include_router(editing.fonts_router)
    # By the route's name, so moving it can't quietly hold uploads to the edit list's limit.
    uploads = app.url_path_for(documents.upload.__name__)
    app.add_middleware(BodyLimit, streamed=[uploads])

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Up and answering."""
        return {"status": "ok"}

    return app
