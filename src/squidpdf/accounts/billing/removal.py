"""Taking an account off only once Stripe bills it nothing: what `squidpdf remove` asks."""

from __future__ import annotations

import time
from typing import Literal

from squidpdf.accounts.deployment import Billing
from squidpdf.accounts.users import Removal, UserStore

# What removing an account came to: the table's word, or, for an account with a customer at
# Stripe, that Stripe bills it still, or that this server can't ask Stripe whether it does.
AccountRemoval = Removal | Literal["subscribed", "unchecked"]


def removal_of(users: UserStore, billing: Billing | None, email: str) -> AccountRemoval:
    """Take the account under `email` off, unless Stripe bills it still, or can't be asked.

    Its own paid time can be late, or a renewal Stripe retries can succeed after it: gone,
    they'd be charged with no account to cancel from. Raises PaymentsUnavailable when
    Stripe can't be asked.
    """
    account = users.find(email)
    if account is not None and account.stripe_customer is not None:
        if billing is None:
            return "unchecked"
        subscriptions = billing.stripe.subscriptions(
            account.stripe_customer, account=account.id
        )
        if subscriptions.live > 0:
            return "subscribed"
    return users.remove(email, now=int(time.time()))
