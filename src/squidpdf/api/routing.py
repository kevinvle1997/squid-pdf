from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

import orjson
from fastapi import Depends, Request, Response
from fastapi.responses import StreamingResponse

from squidpdf.api import pool, rate
from squidpdf.core import Reply, Workers

# What a streamed file is read in: large enough that a file takes few trips to a thread.
_PIECE_BYTES = 1024 * 1024


def controller_with_workers[C](
    cls: Callable[[Workers, Any], C],
) -> Callable[[Workers, Request], C]:
    def with_the_apps_workers(
        workers: Annotated[Workers, Depends(rate.workers_in_turn)], request: Request
    ) -> C:
        return cls(workers, request.app.state.documents)

    return with_the_apps_workers


def controller_with_app_workers[C](cls: Callable[[Workers], C]) -> Callable[[Workers], C]:
    """Takes no address's turn: the first to ask would run everyone's on theirs."""

    def with_the_apps_own_workers(workers: Annotated[Workers, Depends(pool.current)]) -> C:
        return cls(workers)

    return with_the_apps_own_workers


def response_of[T](
    reply: Reply[T], *, media_type: str, set_by: Response | None = None
) -> Response:
    """`set_by`'s headers, a new cookie say: FastAPI adds them only to data it writes itself."""
    response = _response_of(reply, media_type=media_type)
    if set_by is not None:
        response.raw_headers.extend(set_by.raw_headers)
    return response


def _response_of[T](reply: Reply[T], *, media_type: str) -> Response:
    if isinstance(reply.body, io.BufferedIOBase):
        return _streamed(reply.body, reply=reply, media_type=media_type)
    body = reply.body if isinstance(reply.body, bytes) else orjson.dumps(reply.body)
    body_type = media_type if body else None
    return Response(body, status_code=reply.status, headers=reply.headers, media_type=body_type)


def _streamed(
    opened: io.BufferedIOBase, *, reply: Reply[Any], media_type: str
) -> StreamingResponse:
    size = os.fstat(opened.fileno()).st_size
    headers = reply.headers | {"Content-Length": str(size)}
    return StreamingResponse(
        _pieces_of(opened), status_code=reply.status, headers=headers, media_type=media_type
    )


async def _pieces_of(opened: io.BufferedIOBase) -> AsyncIterator[bytes]:
    try:
        while piece := await asyncio.to_thread(opened.read, _PIECE_BYTES):
            yield piece
    finally:
        await asyncio.to_thread(opened.close)


def listed_header(says: str) -> dict[str, Any]:
    return {"description": says, "schema": {"type": "string"}}
