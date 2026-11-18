"""Hearing Stripe's webhook: each event checked, kept in the ledger once, and its paid time set.

What an event says is never trusted alone: billing asks Stripe for the customer's
subscriptions as they stand, so an event sent twice, late or out of order sets the same.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus

from squidpdf.accounts.billing.stripe_webhook import stripe_event_in
from squidpdf.accounts.deployment import Accounts, Billing
from squidpdf.accounts.types import BillingEvent, BillingOutcome, StripeEvent
from squidpdf.accounts.users import UserStore
from squidpdf.core import LogController, LogEvent, NotFound, Reply

_log = LogController.for_module(__name__)

# Stripe's events that can change whether an account's month is paid. Any other is kept in
# the ledger as heard, and Stripe isn't asked.
_PAYMENT_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "invoice.paid",
        "invoice.payment_failed",
    }
)


@dataclass(frozen=True, slots=True, eq=False)
class BillingEventController:
    """Each event Stripe's webhook sends: its signature checked, then kept and applied once."""

    accounts: Accounts

    async def note(self, payload: bytes, *, signature: str | None) -> Reply[bytes]:
        """Keep the event `payload` holds and set its paid time; nothing is sent back.

        Raises NotFound unless Stripe signed it lately, as where no payment is taken;
        PaymentsUnavailable while Stripe can't be asked, so Stripe sends it again later.
        """
        billing = self.accounts.billing
        if billing is None:
            raise NotFound()
        now = int(time.time())
        event = stripe_event_in(payload, signature, secret=billing.webhook_secret, now=now)
        if event is None:
            _log.write(LogEvent.STRIPE_SIGNATURE_REFUSED)
            raise NotFound()
        await billing.run(partial(_apply, self.accounts.users, billing, event))
        return Reply(b"", {}, HTTPStatus.NO_CONTENT)


def _apply(users: UserStore, billing: Billing, event: StripeEvent) -> None:
    """Keep `event` in the ledger and set the paid time it leads to, once; on a billing thread.

    Under the lock from asking Stripe to keeping its answer: a later event's answer can't
    be overwritten by an earlier one's, read before it.
    """
    with billing.applying:
        # Sent again, as Stripe does until it hears back: kept already, so Stripe isn't asked.
        if users.has_billing_event(event.id):
            _log.write(LogEvent.BILLING_EVENT_REPEATED)
            return
        applied = _billing_event(users, billing, event)
        users.apply_billing(applied, now=int(time.time()))
        _log.write(LogEvent.BILLING_EVENT_NOTED, applied=applied.outcome == "applied")


def _billing_event(users: UserStore, billing: Billing, event: StripeEvent) -> BillingEvent:
    """What `event` does: the paid time Stripe says its customer's account has now, if any."""
    if event.type not in _PAYMENT_EVENTS or event.customer is None:
        return _kept(event, outcome="ignored")
    account = users.find_by_customer(event.customer)
    # A customer made in Stripe's dashboard, not by an account going to pay.
    if account is None:
        return _kept(event, outcome="no_account")
    read_at = time.time_ns() // 1_000_000
    paid_until = billing.stripe.paid_until(event.customer)
    return BillingEvent(
        event.id,
        type=event.type,
        created=event.created,
        customer=event.customer,
        account=account.id,
        outcome="applied",
        paid_until=paid_until,
        read_at=read_at,
    )


def _kept(event: StripeEvent, *, outcome: BillingOutcome) -> BillingEvent:
    """`event` as the ledger keeps one that sets no account's paid time."""
    return BillingEvent(
        event.id,
        type=event.type,
        created=event.created,
        customer=event.customer,
        account=None,
        outcome=outcome,
        paid_until=None,
        read_at=None,
    )
