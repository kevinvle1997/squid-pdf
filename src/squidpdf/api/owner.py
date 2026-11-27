"""Another's document answers as one that doesn't exist, never 403, so ids can't be probed."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Request, Response

from squidpdf.core import NotFound

_COOKIE = "__Host-owner"  # __Host-: only this host can set it, so no subdomain plants one
_TOKEN_BYTES = 32


def token(request: Request, response: Response) -> str:
    """FastAPI copies the cookie only onto data a route returns; a `Response` uses `set_by`."""
    existing = request.cookies.get(_COOKIE)
    if existing is not None:
        return existing
    new = secrets.token_urlsafe(_TOKEN_BYTES)
    response.set_cookie(_COOKIE, new, httponly=True, secure=True, samesite="strict")
    return new


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check(request: Request, stored: str) -> None:
    presented = request.cookies.get(_COOKIE)
    owns = presented is not None and hmac.compare_digest(digest(presented), stored)
    if not owns:
        raise NotFound()
