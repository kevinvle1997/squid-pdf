"""Failures that belong to no one feature: routing, requests, and the worker pool."""

from __future__ import annotations

from squidpdf.core import NotFound, Problem, words

__all__ = [
    "InvalidRequest",
    "NotFound",  # core's, so a feature's own errors can build on it
    "RateLimited",
    "RequestTooLarge",
    "ServerError",
    "TooHeavy",
    "TooSlow",
]


class InvalidRequest(Problem):
    """A browser bug: a plain sentence for the user, the particulars in `debug`."""

    type = "invalid_request"
    status = 400
    sentence = words.INVALID_REQUEST


class RequestTooLarge(Problem):
    """An edit list too big to read."""

    type = "request_too_large"
    status = 413
    sentence = words.REQUEST_TOO_LARGE


class TooSlow(Problem):
    """The worker ran out of time: slow, not necessarily broken."""

    type = "too_slow"
    status = 503
    sentence = words.TOO_SLOW


class TooHeavy(Problem):
    """The worker went past the memory ceiling."""

    type = "too_heavy"
    status = 422
    sentence = words.TOO_HEAVY


class ServerError(Problem):
    """A bug: the base's own type, status and sentence."""


# Not raised yet: kept for the browser, which already branches on it.
class RateLimited(Problem):
    """Too many uploads at once."""

    type = "rate_limited"
    status = 429
    sentence = words.RATE_LIMITED
