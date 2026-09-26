"""Every failure the API reports, as RFC 9457 Problem Details.

Each failure is a `core.errors.Problem` subclass that says its own `type`,
status and sentence. `type` is what the browser branches on and `detail` is
shown to the user verbatim. The browser prevents what it can: by the time the
server says no, it's a backstop, never news.
"""

from squidpdf.api.errors.generic import (
    InvalidRequest,
    NotFound,
    RateLimited,
    RequestTooLarge,
    ServerError,
    TooHeavy,
    TooSlow,
)
from squidpdf.api.errors.http import adopt, install, response

__all__ = [
    "InvalidRequest",
    "NotFound",
    "RateLimited",
    "RequestTooLarge",
    "ServerError",
    "TooHeavy",
    "TooSlow",
    "adopt",
    "install",
    "response",
]
