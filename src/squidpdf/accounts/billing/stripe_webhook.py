"""What Stripe's webhook sends: its signature checked against the raw body, then its event."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from squidpdf.accounts import constants
from squidpdf.accounts.types import StripeEvent

# The header's parts: when Stripe signed it, and each signature it made (one for each of the
# endpoint's secrets while one is being rolled over).
_SIGNED_AT = "t"
_SIGNATURE = "v1"


def stripe_event_in(
    payload: bytes, signature: str | None, *, secret: bytes, now: int
) -> StripeEvent | None:
    """The event `payload` holds; None unless `signature` is Stripe's for it, made lately.

    Lately is STRIPE_SIGNATURE_AGE_S either way, so a copy sent again later is refused.
    """
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
    """When the header says Stripe signed, if it says so plainly, and each v1 signature."""
    pairs = [part.partition("=") for part in signature.split(",")]
    signed_at = [value for key, _equals, value in pairs if key == _SIGNED_AT]
    signatures = [value for key, _equals, value in pairs if key == _SIGNATURE]
    # One time, in digits: anything else isn't Stripe's.
    if len(signed_at) != 1 or not (signed_at[0].isascii() and signed_at[0].isdigit()):
        return None, signatures
    return int(signed_at[0]), signatures


def _event_from(said: dict[str, Any]) -> StripeEvent:
    """The parts of Stripe's event billing reads, by name."""
    about = said["data"]["object"]
    # .get: an event about something no customer has, as a price, names none.
    customer = about.get("customer")
    return StripeEvent(
        said["id"],
        type=said["type"],
        created=said["created"],
        # A webhook names its customer by id; anything else is no customer billing knows.
        customer=customer if isinstance(customer, str) else None,
    )
