"""Every failure the API reports, as RFC 9457 Problem Details.

Each failure is a `core.app.errors.Problem` subclass that says its own `type`,
status and sentence. `type` is what the browser branches on and `detail` is
shown to the user verbatim, in the language the browser asked for. The browser
prevents what it can: by the time the server says no, it's a backstop, never
news.
"""

from __future__ import annotations

from squidpdf.api.errors.generic import (
    InvalidRequest,
    MethodNotAllowed,
    NotFound,
    RateLimited,
    RequestTooLarge,
    ServerError,
    TooHeavy,
    TooSlow,
)
from squidpdf.api.errors.http import PROBLEM_RESPONSES, ProblemInfo, adopt, install, response

__all__ = [
    "InvalidRequest",
    "MethodNotAllowed",
    "NotFound",
    "PROBLEM_RESPONSES",
    "ProblemInfo",
    "RateLimited",
    "RequestTooLarge",
    "ServerError",
    "TooHeavy",
    "TooSlow",
    "adopt",
    "install",
    "response",
]
