"""How a route gets its controller, and sends what the controller hands back.

Every feature's controller is built the same way, on the app's workers, and
every Reply goes out the same way: its status, its headers, and the media type
the route names.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import orjson
from fastapi import Depends, Response

from squidpdf.api import pool
from squidpdf.api.pool import Pool
from squidpdf.core import Reply, Workers

__all__ = [
    "controller",
    "response_of",
]


def controller[C](cls: Callable[[Workers], C]) -> Callable[[Pool], C]:
    """A route's dependency that builds `cls` on the app's workers.

    Used as `Depends(controller(RenderController))`.
    """

    def on_the_apps_workers(workers: Annotated[Pool, Depends(pool.current)]) -> C:
        """The controller, on the app's workers."""
        return cls(workers)

    return on_the_apps_workers


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
    typed = media_type if body else None
    response = Response(body, status_code=reply.status, headers=reply.headers, media_type=typed)
    if set_by is not None:
        response.raw_headers.extend(set_by.raw_headers)
    return response
