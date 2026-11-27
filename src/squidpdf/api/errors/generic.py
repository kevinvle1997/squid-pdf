from __future__ import annotations

from squidpdf.core import Problem


class MethodNotAllowed(Problem):
    type = "method_not_allowed"
    status = 405


class RequestTooLarge(Problem):
    type = "request_too_large"
    status = 413


class TooSlow(Problem):
    type = "too_slow"
    status = 503


class NoWorkers(Problem):
    type = "no_workers"
    status = 503


class ServerError(Problem): ...


class RateLimited(Problem):
    type = "rate_limited"
    status = 429
