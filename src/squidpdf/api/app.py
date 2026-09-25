"""The one FastAPI app: shared plumbing, then each feature's router.

The only module that imports a feature's `api.py`. Run it with
`uvicorn squidpdf.api.app:create_app --factory`.
"""

from __future__ import annotations

from fastapi import FastAPI

from squidpdf.api import errors


def create_app() -> FastAPI:
    """A fresh app, so each test gets its own."""
    app = FastAPI(
        title="squid-pdf",
        exception_handlers=errors.HANDLERS,
        # Everything lives under /api; the rest of the host is the frontend's.
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Up and answering."""
        return {"status": "ok"}

    return app
