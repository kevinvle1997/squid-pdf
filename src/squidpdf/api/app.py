"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import Depends, FastAPI

from squidpdf.api.access import AccessLog
from squidpdf.api.body import BodyLimit
from squidpdf.api.disconnect import CancelOnDisconnect
from squidpdf.api.errors import NoWorkers
from squidpdf.api.errors.http import AnswerBugs, ProblemInfo, install
from squidpdf.api.pool import WorkerPool, current, start_pool
from squidpdf.api.rate import RecentUploads
from squidpdf.core import LogController
from squidpdf.documents import api as documents
from squidpdf.editing import api as editing

# Every route can answer with a Problem, so the OpenAPI says so and the browser's types have it.
# `model` puts ProblemInfo in the schemas; the content names the type it's really sent as.
_PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    "default": {
        "model": ProblemInfo,
        "description": "Problem Details: what went wrong, in the reader's words",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemInfo"}}
        },
    },
}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Workers and the expiry sweeper start with the app and stop with it."""
    # Made here, in the server's one event loop: a pool works only in the loop it's made in.
    app.state.pool = start_pool()
    # Uploads by address, kept per app, so each app (a test's too) counts its own.
    app.state.recent_uploads = RecentUploads()
    sweeper = asyncio.create_task(documents.sweep_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        # Awaited, so it ends before the app does.
        with suppress(asyncio.CancelledError):  # raised: it was cancelled
            await sweeper
        await app.state.pool.close()


def create_app() -> FastAPI:
    """A fresh app, so each test gets its own."""
    LogController.configure()
    app = FastAPI(
        title="squid-pdf",
        lifespan=_lifespan,
        # Everything lives under /api; the rest of the host is the frontend's.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    install(app)
    app.include_router(documents.router, responses=_PROBLEM_RESPONSES)
    app.include_router(editing.router, responses=_PROBLEM_RESPONSES)
    app.include_router(editing.fonts_router, responses=_PROBLEM_RESPONSES)
    # By the route's name, so moving it can't quietly hold uploads to the edit list's limit.
    upload_path = app.url_path_for(documents.upload.__name__)
    app.add_middleware(BodyLimit, streamed=frozenset([upload_path]))
    # Outside BodyLimit, so it sees the body come in however BodyLimit reads it.
    app.add_middleware(CancelOnDisconnect)

    @app.get("/api/health", responses=_PROBLEM_RESPONSES)
    async def health(pool: Annotated[WorkerPool, Depends(current)]) -> dict[str, str]:
        """Up and answering."""
        # The docstring stays: it's the schema's description, which the browser's types copy.
        # Up: a worker can take a task. A broken pool is replaced first; none can start: 503.
        if not await pool.ready():
            raise NoWorkers()
        return {"status": "ok"}

    # Just inside the access log, so a bug's 500 is logged by it, and never raised past it.
    app.add_middleware(AnswerBugs)
    # Outermost, so a request its browser left is logged too. The healthcheck's every
    # 30 s isn't: a failing one shows in `docker ps`.
    app.add_middleware(AccessLog, unlogged=frozenset([app.url_path_for(health.__name__)]))
    return app
