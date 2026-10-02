"""How a route gets its controller, and sends what the controller hands back.

Every feature's controller is built the same way, with the app's workers (the
pool of processes PDF work runs in), and every Reply goes out the same way: its
status, its headers, and the media type the route names. A header of our own
is listed in the route's `responses`, so the OpenAPI says it's there.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import orjson
from fastapi import Depends, Response

from squidpdf.api import pool
from squidpdf.core import Reply, Workers


def controller_with_workers[C](cls: Callable[[Workers], C]) -> Callable[[Workers], C]:
    """A route's dependency: a new `cls` for each request, built with the app's workers.

    Used as `Depends(controller_with_workers(RenderController))`. Made once, when
    the route is defined; FastAPI calls what it returns on every request.
    """

    def with_the_apps_workers(workers: Annotated[Workers, Depends(pool.current)]) -> C:
        """The controller, built with the app's workers."""
        return cls(workers)

    return with_the_apps_workers


def response_of[T](
    reply: Reply[T], *, media_type: str, set_by: Response | None = None
) -> Response:
    """`reply` as the Response the route sends, typed `media_type`.

    Bytes go out as they are; anything else is written out as JSON. `set_by`
    is the Response a route's dependencies set headers on, such as a new
    cookie: FastAPI adds those only to data it writes out itself.
    """
    body = reply.body if isinstance(reply.body, bytes) else orjson.dumps(reply.body)
    # No body, no body's type: a 304 says the browser's copy is current.
    body_type = media_type if body else None
    response = Response(
        body, status_code=reply.status, headers=reply.headers, media_type=body_type
    )
    if set_by is not None:
        response.raw_headers.extend(set_by.raw_headers)
    return response


def listed_header(says: str) -> dict[str, Any]:
    """A header a reply carries, as a route's `responses` lists it in the OpenAPI: text."""
    return {"description": says, "schema": {"type": "string"}}
