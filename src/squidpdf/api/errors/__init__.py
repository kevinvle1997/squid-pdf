"""Every failure the API reports, as RFC 9457 Problem Details.

Each failure is a `core.app.errors.Problem` subclass that says its own `type`,
status and sentence. These are the API's own; core's (`NotFound`,
`InvalidRequest`, `TooHeavy` and the rest) are imported from `squidpdf.core`.
`type` is what the browser branches on and `detail` is shown to the user
verbatim, in the language the browser asked for. The browser prevents what it
can: by the time the server says no, it's a backstop, never news.

How a Problem leaves as Problem Details, and what a foreign exception means, is
`http.py`'s: imported by name, since it reads the API's constants, which import
these.
"""

from __future__ import annotations

from squidpdf.api.errors.generic import (
    MethodNotAllowed,
    NoWorkers,
    RateLimited,
    RequestTooLarge,
    ServerError,
    TooSlow,
)

__all__ = [
    "MethodNotAllowed",
    "NoWorkers",
    "RateLimited",
    "RequestTooLarge",
    "ServerError",
    "TooSlow",
]
