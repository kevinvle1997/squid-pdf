"""Hearing Stripe's webhook: each event checked, kept in the ledger once, and its paid time set.

What an event says is never trusted alone: billing asks Stripe for the customer's
subscriptions as they stand, and the latest answer asked for stands, so an event sent
twice, late or out of order sets the same. `resync` asks again by hand, kept as an event.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from functools import partial
from http import HTTPStatus

from squidpdf.accounts.billing.stripe_signature import stripe_event_in
from squidpdf.accounts.deployment import Accounts, Billing
from squidpdf.accounts.types import BillingEvent, BillingOutcome, StripeEvent
from squidpdf.accounts.users import User, UserStore
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
# Every event about a dispute, which whoever runs the server answers in Stripe.
_DISPUTE_EVENTS = "charge.dispute."
# A dispute opened: its account's own subscriptions end at once, so no charge follows it.
_DISPUTE_OPENED = "charge.dispute.created"
# What a resync by hand is kept in the ledger as, beside Stripe's own events.
_RESYNC = "squidpdf.resync"


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


def resync(
    users: UserStore, billing: Billing, account: User, *, customer: str
) -> BillingOutcome | None:
    """Ask Stripe again for `account`'s paid time, by its `customer`, and keep it as our event.

    For whoever runs the server, when an event was lost: Stripe's own resent keeps its id,
    which the ledger has already. Raises PaymentsUnavailable when Stripe can't be asked.
    """
    now = int(time.time())
    asked = StripeEvent(
        f"{_RESYNC}-{now}-{account.id}",
        type=_RESYNC,
        created=now,
        customer=customer,
        charge=None,
    )
    paid = _paid_event(billing, asked, account=account, customer=customer)
    return users.apply_billing(paid, now=now)


def _apply(users: UserStore, billing: Billing, event: StripeEvent) -> None:
    """Keep `event` in the ledger, once, and set the paid time it leads to."""
    # Sent again, as Stripe does until it hears back: kept already, so Stripe isn't asked.
    if users.has_ledger_event(event.id):
        _log.write(LogEvent.BILLING_EVENT_REPEATED)
        return
    if event.type.startswith(_DISPUTE_EVENTS):
        _log.write(LogEvent.BILLING_DISPUTED)
    kept = users.apply_billing(_billing_event(users, billing, event), now=int(time.time()))
    _log.write(LogEvent.BILLING_EVENT_NOTED, applied=kept == "applied")


def _billing_event(users: UserStore, billing: Billing, event: StripeEvent) -> BillingEvent:
    """What `event` does: the paid time Stripe says its customer's account has now, if any."""
    if event.type == _DISPUTE_OPENED:
        return _disputed(users, billing, event)
    if event.type not in _PAYMENT_EVENTS or event.customer is None:
        return _kept(event, outcome="ignored")
    account = _account_of(users, event.customer)
    if account is None:
        return _kept(event, outcome="no_account")
    return _paid_event(billing, event, account=account, customer=event.customer)


def _disputed(users: UserStore, billing: Billing, dispute: StripeEvent) -> BillingEvent:
    """What a dispute opened does: its account's own subscriptions ended, its paid time read."""
    # A dispute names its charge, not its customer: Stripe is asked whose the charge was.
    charge = dispute.charge
    customer = None if charge is None else billing.stripe.customer_of_charge(charge)
    with_customer = replace(dispute, customer=customer)  # so the ledger keeps whose it was
    # A charge to no customer: none of the server's checkouts made it.
    if customer is None:
        return _kept(with_customer, outcome="ignored")
    account = _account_of(users, customer)
    if account is None:
        return _kept(with_customer, outcome="no_account")
    _cancel_own(billing, with_customer, account=account, customer=customer)
    return _paid_event(billing, with_customer, account=account, customer=customer)


def _account_of(users: UserStore, customer: str) -> User | None:
    """The account Stripe's `customer` is; None, said in the log, for one no account has."""
    account = users.find_by_customer(customer)
    # A customer made in Stripe's dashboard, or kept by an account removed since: it may be
    # billed with no account to show for it, so whoever runs the server looks in Stripe.
    if account is None:
        _log.write(LogEvent.BILLING_CUSTOMER_UNKNOWN)
    return account


def _cancel_own(billing: Billing, event: StripeEvent, *, account: User, customer: str) -> None:
    """End each of `account`'s own subscriptions made before the dispute that bills, or may yet.

    Stripe sends a dispute again until it hears back: one bought since is paid for afresh.
    """
    own = billing.stripe.subscriptions(customer, account=account.id).own_unended
    # The event's id ties Stripe's record of each to the ledger's row.
    why = f"squidpdf: a charge was disputed ({event.id})"
    for subscription in own:
        if subscription.created <= event.created:
            billing.stripe.cancel_subscription(subscription.id, why=why)


def _paid_event(
    billing: Billing, event: StripeEvent, *, account: User, customer: str
) -> BillingEvent:
    """`event` with the paid time Stripe says `account` has now, and when it was asked."""
    paid_read_at = time.time_ns() // 1_000_000
    subscriptions = billing.stripe.subscriptions(customer, account=account.id)
    # Two that bill at once: it's paying twice, which whoever runs the server mends in Stripe.
    if subscriptions.unended > 1:
        _log.write(LogEvent.BILLING_SUBSCRIBED_TWICE, unended=subscriptions.unended)
    return BillingEvent(
        event.id,
        type=event.type,
        created=event.created,
        customer=customer,
        account=account.id,
        outcome="applied",
        paid_until=subscriptions.paid_until,
        paid_read_at=paid_read_at,
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
        paid_read_at=None,
    )
