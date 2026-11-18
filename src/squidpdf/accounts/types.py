"""What the browser is told about accounts, and what Brevo and Stripe tell the server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class SignedIn(TypedDict):
    """The account this browser is signed in to."""

    email: str
    is_unlimited: bool  # uses as many documents as they like
    paid_until: int | None  # paid for no document limit until then, Unix seconds; None if not
    has_billing: bool  # has gone to pay, so Stripe's portal has their billing


class AccountState(TypedDict):
    """Whether this server offers accounts, and which this browser is signed in to."""

    offered: bool  # false on a server that keeps none, as a self-hosted copy
    invite_only: bool  # only invited people may sign in, and only they open documents
    takes_payment: bool  # an account may pay for no document limit
    signed_in: SignedIn | None


class PaymentPage(TypedDict):
    """Stripe's page this browser goes to next: its checkout, or its portal."""

    url: str


class LinkSent(TypedDict):
    """A sign-in link is on its way: where, in the reader's words."""

    sentence: str


@dataclass(frozen=True, slots=True)
class MailEvent:
    """What Brevo's webhook says happened to an email: its word for it, and the address."""

    event: str
    email: str


@dataclass(frozen=True, slots=True)
class StripeEvent:
    """What Stripe's webhook said happened, its signature checked: the parts billing reads."""

    id: str
    type: str  # Stripe's word for it, as invoice.paid
    created: int  # when Stripe made it, Unix seconds
    customer: str | None  # the Stripe customer it's about; None for one about none
    charge: str | None  # the charge it's about, as a dispute names one; None for one about none


@dataclass(frozen=True, slots=True)
class OwnSubscription:
    """An account's own subscription that bills, or may yet: Stripe's id, and its age."""

    id: str
    created: int  # when Stripe made it, Unix seconds


@dataclass(frozen=True, slots=True)
class Subscriptions:
    """What Stripe has of an account's subscriptions now: paid time, how many bill, its own."""

    paid_until: int | None  # the latest paid month's end of the account's own; None if none
    unended: int  # its customer's not cancelled or expired, which bill, or may yet
    own_unended: tuple[OwnSubscription, ...]  # the account's own of those


# What a billing event did, as the ledger keeps it: superseded is Stripe asked before the
# paid time kept already was, so it set nothing.
BillingOutcome = Literal["applied", "superseded", "no_account", "ignored"]


@dataclass(frozen=True, slots=True)
class BillingEvent:
    """One event Stripe sent, as the ledger keeps it: what it was, and what it did."""

    id: str  # Stripe's own, so the same event sent again is the same row
    type: str  # Stripe's word for it, as customer.subscription.updated
    created: int  # when Stripe made it, Unix seconds
    customer: str | None  # the Stripe customer it's about; None for one about none
    account: str | None  # the account whose paid time it set; None where it set none
    outcome: BillingOutcome
    paid_until: int | None  # the paid time Stripe said, Unix seconds; None for none
    paid_read_at: (
        int | None
    )  # when Stripe was asked for it, Unix ms; None where it wasn't asked
