"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from squidpdf.api.body import BodyLimit
from squidpdf.api.disconnect import CancelOnDisconnect
from squidpdf.api.errors import NoWorkers
from squidpdf.api.errors.http import PROBLEM_RESPONSES, install
from squidpdf.api.pool import WorkerPool, current
from squidpdf.api.rate import RecentUploads
from squidpdf.documents import api as documents
from squidpdf.editing import api as editing

__all__ = [
    "create_app",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Workers and the expiry sweeper start with the app and stop with it."""
    # Made here, in the server's one event loop: a pool works only in the loop it's made in.
    app.state.pool = WorkerPool()
    # Uploads by address, kept per app, so each app (a test's too) counts its own.
    app.state.recent_uploads = RecentUploads()
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
    install(app)
    app.include_router(documents.router, responses=PROBLEM_RESPONSES)
    app.include_router(editing.router, responses=PROBLEM_RESPONSES)
    app.include_router(editing.fonts_router, responses=PROBLEM_RESPONSES)
    # By the route's name, so moving it can't quietly hold uploads to the edit list's limit.
    upload_path = app.url_path_for(documents.upload.__name__)
    app.add_middleware(BodyLimit, streamed=[upload_path])
    # Outermost, so it sees the body come in however BodyLimit reads it.
    app.add_middleware(CancelOnDisconnect)

    @app.get("/api/health", responses=PROBLEM_RESPONSES)
    async def health(pool: Annotated[WorkerPool, Depends(current)]) -> dict[str, str]:
        """Up and answering."""
        # The docstring stays: it's the schema's description, which the browser's types copy.
        # Up: a worker can take a task. A broken pool is replaced first; none can start: 503.
        if not await pool.ready():
            raise NoWorkers()
        return {"status": "ok"}

    return app
