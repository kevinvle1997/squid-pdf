"""Failures that belong to no one feature: routing, requests, and the worker pool."""

from __future__ import annotations

from squidpdf.core import InvalidRequest, NotFound, Problem, TooHeavy

__all__ = [
    "InvalidRequest",  # core's, so controllers can raise it too
    "NotFound",  # core's, so a feature's own errors can build on it
    "RateLimited",
    "RequestTooLarge",
    "ServerError",
    "TooHeavy",  # core's, so a worker can raise it too
    "TooSlow",
]


class RequestTooLarge(Problem):
    """An edit list too big to read."""

    type = "request_too_large"
    status = 413


class TooSlow(Problem):
    """The worker ran out of time: slow, not necessarily broken."""

    type = "too_slow"
    status = 503


class ServerError(Problem):
    """A bug: the base's own type, status and sentence."""


# Not raised yet: kept for the browser, which already branches on it.
class RateLimited(Problem):
    """Too many uploads at once."""

    type = "rate_limited"
    status = 429
