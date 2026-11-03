"""A value signed with the server's secret, for a purpose and until a time, then checked.

What a sign-in link and the signed-in cookie carry: nothing is stored, so the signature
is the only proof the server wrote it. The purpose is signed too, so one kind is never
taken for the other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Literal

import orjson

# What a value is signed for: a link that signs someone in, or the cookie that says they are.
Purpose = Literal["link", "signed_in"]
_SEPARATOR = "."


def signed(value: str, *, purpose: Purpose, until: int, secret: bytes) -> str:
    """`value`, good for `purpose` until `until` (Unix seconds), as text safe in a URL."""
    payload = _encoded(orjson.dumps({"value": value, "until": until}))
    return f"{payload}{_SEPARATOR}{_signature(payload, purpose=purpose, secret=secret)}"


def value_of(token: str, *, purpose: Purpose, now: int, secret: bytes) -> str | None:
    """The value `token` was signed with for `purpose`; None if past, altered or not ours."""
    payload, _, signature = token.partition(_SEPARATOR)
    expected = _signature(payload, purpose=purpose, secret=secret)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        read = orjson.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:  # raised for bytes we signed but can't read: an older shape
        return None
    # Its time is past.
    if read["until"] <= now:
        return None
    return read["value"]


def _signature(payload: str, *, purpose: Purpose, secret: bytes) -> str:
    """`payload`'s HMAC for `purpose`, as text safe in a URL."""
    signing = f"{purpose}{_SEPARATOR}{payload}".encode()
    return _encoded(hmac.digest(secret, signing, hashlib.sha256))


def _encoded(data: bytes) -> str:
    """`data` as URL-safe base64, its padding dropped."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
