from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from squidpdf.accounts.deployment import Billing
from squidpdf.accounts.users import Removal, UserStore

AccountRemoval = Removal | Literal["unchecked"]


def remove_account(
    users: UserStore, email: str, *, secret: bytes, billing_here: Callable[[], Billing | None]
) -> AccountRemoval:
    account = users.find(email)
    if account is None or account.stripe_customer is None:
        return users.remove(email, now=int(time.time()), secret=secret)
    billing = billing_here()
    if billing is None:
        return "unchecked"
    if billing.stripe.subscriptions(account.stripe_customer, account=account.id).unended > 0:
        return "paying"
    return users.remove(email, now=int(time.time()), secret=secret)
