"""The accounts' cookies, and the one way their Set-Cookie headers are written.

Every one is Secure, HttpOnly and SameSite=Strict, under __Host-, so only this host sets
it, only over HTTPS, no script reads it and no other site's page sends it.
"""

from __future__ import annotations

from http.cookies import SimpleCookie

SIGNED_IN = "__Host-signed-in"  # which account this browser is signed in to
COUNTED = "__Host-counted"  # the files this browser opened without an account


def cookie_header(name: str, value: str, *, max_age: int) -> str:
    """The Set-Cookie header keeping `value` under `name` for `max_age` seconds; 0 clears it."""
    cookie: SimpleCookie = SimpleCookie()
    cookie[name] = value
    kept = cookie[name]
    kept["path"] = "/"
    kept["max-age"] = max_age
    kept["secure"] = True
    kept["httponly"] = True
    kept["samesite"] = "Strict"
    return kept.OutputString()
