from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from squidpdf.accounts import api as accounts_api, constants as accounts_constants
from squidpdf.accounts.deployment import deployed_accounts
from squidpdf.accounts.metering import AccountMeters
from squidpdf.api.access import AccessLog
from squidpdf.api.body import BodyLimit
from squidpdf.api.disconnect import CancelOnDisconnect
from squidpdf.api.errors import NoWorkers
from squidpdf.api.errors.http import BugBoundary, ProblemInfo, install
from squidpdf.api.pool import WorkerPool, current, start_pool
from squidpdf.api.rate import RecentStarts, Turns
from squidpdf.core import LogController
from squidpdf.documents import api as documents_api
from squidpdf.documents.meters import UNMETERED
from squidpdf.documents.store import FOLDER_STORE, DocumentStore
from squidpdf.editing import api as editing_api
from squidpdf.terms import api as terms_api
from squidpdf.terms.terms_file import deployed_terms

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
    # Made here, in the server's one event loop: a pool works only in the loop it's made in.
    app.state.pool = start_pool()
    app.state.recent_uploads = RecentStarts()
    app.state.recent_links = RecentStarts()
    app.state.recent_payment_pages = RecentStarts()
    app.state.recent_links_to = RecentStarts(window_s=accounts_constants.LINK_VALID_S)
    app.state.upload_turns = Turns()
    app.state.job_turns = Turns()
    sweeper = asyncio.create_task(documents_api.sweep_forever(app.state.documents))
    editing_api.measure_font_list_ahead(app.state.pool)
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        await app.state.pool.close()


def create_app(documents: DocumentStore = FOLDER_STORE) -> FastAPI:
    LogController.configure()
    app = FastAPI(
        title="squid-pdf",
        lifespan=_lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.documents = documents
    app.state.terms = deployed_terms()
    accounts = app.state.accounts = deployed_accounts()
    app.state.meters = (
        UNMETERED
        if accounts is None or accounts.allowance is None
        else AccountMeters(accounts, accounts.allowance)
    )
    install(app)
    _include_features(app)
    app.add_middleware(BodyLimit, streamed=_streamed_routes())
    # Outside BodyLimit, so it sees the body come in however BodyLimit reads it.
    app.add_middleware(CancelOnDisconnect)

    @app.get("/api/health", responses=_PROBLEM_RESPONSES)
    async def health(pool: Annotated[WorkerPool, Depends(current)]) -> dict[str, str]:
        if not await pool.ready():
            raise NoWorkers()
        return {"status": "ok"}

    # Just inside the access log, so a bug's 500 is logged by it, and never raised past it.
    app.add_middleware(BugBoundary)
    # Outermost, so a request its browser left is logged too.
    app.add_middleware(AccessLog, unlogged=frozenset([app.url_path_for(health.__name__)]))
    return app


def _include_features(app: FastAPI) -> None:
    invited = [Depends(accounts_api.admit_invited)]
    app.include_router(documents_api.router, responses=_PROBLEM_RESPONSES, dependencies=invited)
    app.include_router(editing_api.router, responses=_PROBLEM_RESPONSES, dependencies=invited)
    app.include_router(
        editing_api.fonts_router, responses=_PROBLEM_RESPONSES, dependencies=invited
    )
    app.include_router(terms_api.router, responses=_PROBLEM_RESPONSES)
    app.include_router(accounts_api.router, responses=_PROBLEM_RESPONSES)


def _streamed_routes() -> tuple[APIRoute, ...]:
    streamed = {documents_api.upload.__name__, documents_api.attach_font.__name__}
    routes = tuple(
        route
        for route in documents_api.router.routes
        if isinstance(route, APIRoute) and route.name in streamed
    )
    # A name that no longer finds its route would hold that body to the edit list's limit.
    if len(routes) != len(streamed):
        raise RuntimeError(f"routes that stream their own body: {sorted(streamed)}, not found")
    return routes
