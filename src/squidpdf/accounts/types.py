from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class DocumentsLeft(TypedDict):
    left: int
    monthly: int


class SignedIn(TypedDict):
    email: str
    is_unlimited: bool
    paid_until: int | None
    has_billing: bool
    documents_left: DocumentsLeft | None


class AccountState(TypedDict):
    offered: bool
    invite_only: bool
    takes_payment: bool
    signed_in: SignedIn | None


class PaymentPage(TypedDict):
    url: str


class LinkSent(TypedDict):
    sentence: str


@dataclass(frozen=True, slots=True)
class MailEvent:
    event: str
    email: str


@dataclass(frozen=True, slots=True)
class StripeEvent:
    id: str
    type: str
    created: int
    customer: str | None
    charge: str | None


@dataclass(frozen=True, slots=True)
class OwnSubscription:
    id: str
    created: int


@dataclass(frozen=True, slots=True)
class Subscriptions:
    paid_until: int | None
    unended: int
    own_unended: tuple[OwnSubscription, ...]


BillingOutcome = Literal["applied", "superseded", "no_account", "ignored"]


@dataclass(frozen=True, slots=True)
class BillingEvent:
    id: str
    type: str
    created: int
    customer: str | None
    account: str | None
    outcome: BillingOutcome
    paid_until: int | None
    paid_read_at: int | None
