"""Taking an account off only once Stripe bills it nothing: what `squidpdf remove` asks."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from squidpdf.accounts.deployment import Billing
from squidpdf.accounts.users import Removal, UserStore

# What removing an account came to: the table's word, or that the account has a customer at
# Stripe, and this server can't ask Stripe whether it bills it still.
AccountRemoval = Removal | Literal["unchecked"]


def remove_account(
    users: UserStore, email: str, *, secret: bytes, billing_here: Callable[[], Billing | None]
) -> AccountRemoval:
    """Take the account under `email` off, unless Stripe bills it still, or can't be asked.

    `billing_here` is asked only for an account with a customer at Stripe. Raises
    PaymentsUnavailable when Stripe can't be asked.
    """
    account = users.find(email)
    # No customer at Stripe: nothing there bills it.
    if account is None or account.stripe_customer is None:
        return users.remove(email, now=int(time.time()), secret=secret)
    billing = billing_here()
    if billing is None:
        return "unchecked"
    if billing.stripe.subscriptions(account.stripe_customer, account=account.id).unended > 0:
        return "paying"
    return users.remove(email, now=int(time.time()), secret=secret)
