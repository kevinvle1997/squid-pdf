"""What the browser is told about accounts, and what Brevo and Stripe tell the server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class SignedIn(TypedDict):
    """The account this browser is signed in to."""

    email: str
    is_unlimited: bool  # uses as many documents as they like


class AccountState(TypedDict):
    """Whether this server offers accounts, and which this browser is signed in to."""

    offered: bool  # false on a server that keeps none, as a self-hosted copy
    invite_only: bool  # only invited people may sign in, and only they open documents
    signed_in: SignedIn | None


class LinkSent(TypedDict):
    """A sign-in link is on its way: where, in the reader's words."""

    sentence: str


@dataclass(frozen=True, slots=True)
class MailEvent:
    """What Brevo's webhook says happened to an email: its word for it, and the address."""

    event: str
    email: str


# What a billing event did: set an account's paid time; read Stripe before a read kept already,
# so it set nothing; named a customer no account has; or was of a kind billing doesn't act on.
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
    read_at: int | None  # when Stripe was asked for it, Unix ms; None where it wasn't asked
