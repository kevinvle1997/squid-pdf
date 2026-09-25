"""Who a document belongs to: one random cookie per browser, never an account.

A document stores only the cookie's SHA-256, so a leaked id or folder is useless
without the browser that uploaded it. Someone else's document answers exactly
like one that doesn't exist, not_found and never 403, so ids can't be probed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Request, Response

from squidpdf.api.errors import ApiError, Problem

# `__Host-` makes the browser refuse it unless it's Secure, on /, and set by
# this host, so a sibling subdomain can't plant one.
COOKIE = "__Host-owner"
_TOKEN_BYTES = 32


def token(request: Request, response: Response) -> str:
    """This browser's owner token, setting a new cookie on `response` if it has none.

    As a dependency, FastAPI copies the cookie onto the reply only if the route
    returns data; a route that returns a `Response` itself must set it there.
    """
    existing = request.cookies.get(COOKIE)  # None on a browser's first upload
    if existing is not None:
        return existing
    new = secrets.token_urlsafe(_TOKEN_BYTES)
    response.set_cookie(COOKIE, new, httponly=True, secure=True, samesite="strict")
    return new


def digest(token: str) -> str:
    """What a document stores in place of the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def check(request: Request, stored: str) -> None:
    """Raise not_found unless this browser's cookie is the one `stored` came from."""
    presented = request.cookies.get(COOKIE)  # None if this browser never uploaded
    if presented is None or not hmac.compare_digest(digest(presented), stored):
        raise ApiError(Problem.NOT_FOUND)
