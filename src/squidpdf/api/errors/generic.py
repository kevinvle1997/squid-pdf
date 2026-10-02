"""Failures that belong to no one feature: routing, requests, and the worker pool."""

from __future__ import annotations

from squidpdf.core import Problem


class MethodNotAllowed(Problem):
    """A path asked with a method it doesn't take: a browser bug, said as one."""

    type = "method_not_allowed"
    status = 405


class RequestTooLarge(Problem):
    """An edit list too big to read."""

    type = "request_too_large"
    status = 413


class TooSlow(Problem):
    """The worker ran out of time: slow, not necessarily broken."""

    type = "too_slow"
    status = 503


class NoWorkers(Problem):
    """No worker can start or stay up, so no PDF work can be done now."""

    type = "no_workers"
    status = 503


class ServerError(Problem):
    """A bug: the base's own type, status and sentence."""


# Not raised yet: kept for the browser, which already branches on it.
class RateLimited(Problem):
    """Too many uploads at once."""

    type = "rate_limited"
    status = 429
