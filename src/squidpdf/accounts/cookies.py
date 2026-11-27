"""Secure, HttpOnly, SameSite=Strict, under __Host-: only this host sets one, over HTTPS."""

from __future__ import annotations

from http.cookies import SimpleCookie

SIGNED_IN = "__Host-signed-in"
COUNTED = "__Host-counted"


def cookie_header(name: str, value: str, *, max_age: int) -> str:
    cookie: SimpleCookie = SimpleCookie()
    cookie[name] = value
    kept = cookie[name]
    kept["path"] = "/"
    kept["max-age"] = max_age
    kept["secure"] = True
    kept["httponly"] = True
    kept["samesite"] = "Strict"
    return kept.OutputString()
