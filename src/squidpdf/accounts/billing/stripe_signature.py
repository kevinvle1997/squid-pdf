from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from squidpdf.accounts import constants
from squidpdf.accounts.types import StripeEvent

_SIGNED_AT = "t"
_SIGNATURE = "v1"
# Ten digits until the year 2286: a longer one isn't Stripe's.
_MOST_TIME_DIGITS = 12


def stripe_event_in(
    payload: bytes, signature: str | None, *, secret: bytes, now: int
) -> StripeEvent | None:
    """Within STRIPE_SIGNATURE_AGE_S either way, so a copy sent again later is refused."""
    if signature is None:
        return None
    signed_at, signatures = _parts_of(signature)
    if signed_at is None or abs(now - signed_at) > constants.STRIPE_SIGNATURE_AGE_S:
        return None
    signed = f"{signed_at}.".encode() + payload
    expected = hmac.new(secret, signed, hashlib.sha256).hexdigest().encode()
    # Compared in constant time, so a signature can't be guessed a letter at a time.
    if not any(hmac.compare_digest(expected, each.encode()) for each in signatures):
        return None
    return _event_from(json.loads(payload))


def _parts_of(signature: str) -> tuple[int | None, list[str]]:
    pairs = [part.partition("=") for part in signature.split(",")]
    signed_at = [value for key, _equals, value in pairs if key == _SIGNED_AT]
    signatures = [value for key, _equals, value in pairs if key == _SIGNATURE]
    if len(signed_at) != 1 or not _is_time(signed_at[0]):
        return None, signatures
    return int(signed_at[0]), signatures


def _is_time(text: str) -> bool:
    return text.isascii() and text.isdigit() and len(text) <= _MOST_TIME_DIGITS


def _event_from(said: dict[str, Any]) -> StripeEvent:
    about = said["data"]["object"]
    customer = about.get("customer")
    charge = about.get("charge")
    return StripeEvent(
        said["id"],
        type=said["type"],
        created=said["created"],
        customer=customer if isinstance(customer, str) else None,
        charge=charge if isinstance(charge, str) else None,
    )
