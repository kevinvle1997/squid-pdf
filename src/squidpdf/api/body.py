"""What a request says about its body before any of it is read."""

from __future__ import annotations

from fastapi import Request

from squidpdf.core import InvalidRequest

__all__ = [
    "declared_size",
]

# An exabyte: no body comes near it, and int() refuses a number past 4300 digits.
_MAX_DIGITS = 18


def declared_size(request: Request) -> int | None:
    """The body's size as the request states it, or None; InvalidRequest if not a number."""
    declared = request.headers.get("content-length")  # absent when the body is chunked
    if declared is None:
        return None
    digits = declared.strip()
    # Digits only: int() would also take "-5" and "1_000".
    readable = digits.isascii() and digits.isdigit() and len(digits) <= _MAX_DIGITS
    if not readable:
        shown = declared[:_MAX_DIGITS]
        raise InvalidRequest(debug=f"content-length isn't a size in bytes; it starts {shown!r}")
    return int(digits)
