"""Every failure the API reports, as RFC 9457 Problem Details.

Each is a `Problem` subclass naming its `type` (which the browser branches on), status
and sentence; core's come from `squidpdf.core`. `http.py`, which sends them, is imported
by name, not from here: it reads the API's constants, which import these.
"""

from __future__ import annotations

from squidpdf.api.errors.generic import (
    MethodNotAllowed as MethodNotAllowed,
    NoWorkers as NoWorkers,
    RateLimited as RateLimited,
    RequestTooLarge as RequestTooLarge,
    ServerError as ServerError,
    TooSlow as TooSlow,
)
