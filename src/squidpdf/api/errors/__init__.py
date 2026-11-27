"""`http.py` is imported by name: it reads the API's constants, which import these."""

from __future__ import annotations

from squidpdf.api.errors.generic import (
    MethodNotAllowed as MethodNotAllowed,
    NoWorkers as NoWorkers,
    RateLimited as RateLimited,
    RequestTooLarge as RequestTooLarge,
    ServerError as ServerError,
    TooSlow as TooSlow,
)
