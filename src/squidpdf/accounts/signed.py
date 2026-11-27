"""Nothing is stored, so the signature is the only proof.

The purpose is signed too, so one kind is never taken for the other.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Literal

import orjson

_Purpose = Literal["link", "signed_in", "counted", "bounced"]
_SEPARATOR = "."


@dataclass(frozen=True, slots=True)
class Signed:
    value: str
    until: int


def signed(value: str, *, purpose: _Purpose, until: int, secret: bytes) -> str:
    payload = _encoded(orjson.dumps({"value": value, "until": until}))
    return f"{payload}{_SEPARATOR}{_signature(payload, purpose=purpose, secret=secret)}"


def read_signed(token: str, *, purpose: _Purpose, now: int, secret: bytes) -> Signed | None:
    payload, _, signature = token.partition(_SEPARATOR)
    expected = _signature(payload, purpose=purpose, secret=secret)
    # As bytes: compare_digest raises, not answers, for text with a letter past ASCII.
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    try:
        read = orjson.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if read["until"] <= now:
        return None
    return Signed(read["value"], read["until"])


def digest_of(value: str, *, purpose: _Purpose, secret: bytes) -> str:
    return _signature(value, purpose=purpose, secret=secret)


def _signature(payload: str, *, purpose: _Purpose, secret: bytes) -> str:
    signing = f"{purpose}{_SEPARATOR}{payload}".encode()
    return _encoded(hmac.digest(secret, signing, hashlib.sha256))


def _encoded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
