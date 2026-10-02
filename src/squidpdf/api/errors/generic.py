"""Failures that belong to no one feature: routing, requests, and the worker pool."""

from __future__ import annotations

from squidpdf.core import Problem


class MethodNotAllowed(Problem):
    """A path asked with a method it doesn't take: a browser bug, said as one."""

    type = "method_not_allowed"
    status = 405


class RequestTooLarge(Problem):
    """A request body past `MAX_BODY_BYTES`: any but an upload's, which checks its own size."""

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


class RateLimited(Problem):
    """Too many uploads from one address in a minute: `UPLOADS_PER_MINUTE`."""

    type = "rate_limited"
    status = 429
