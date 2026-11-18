"""Going to pay: Stripe's checkout for an account's first month, its portal after.

Each hands back where the browser goes next, a page of Stripe's: no card reaches this server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus

from squidpdf.accounts.deployment import Accounts, Billing
from squidpdf.accounts.errors import AlreadyUnlimited, NeverPaid, NoPayments, SignInToPay
from squidpdf.accounts.types import PaymentPage
from squidpdf.accounts.users import User, UserStore
from squidpdf.core import Reply


@dataclass(frozen=True, slots=True, eq=False)
class CheckoutController:
    """Sending a signed-in account to Stripe's checkout, to pay monthly for no limit."""

    accounts: Accounts

    async def checkout(self, user: User | None) -> Reply[PaymentPage]:
        """Where `user` pays: Stripe's checkout, for the account's customer, made on first use.

        Raises NoPayments, SignInToPay, or AlreadyUnlimited for an account with no limit to
        lift: it would pay twice. PaymentsUnavailable while Stripe can't be asked.
        """
        billing = _billing_of(self.accounts)
        if user is None:
            raise SignInToPay()
        if user.is_unlimited or user.is_paid_at(int(time.time())):
            raise AlreadyUnlimited()
        paying = partial(
            _checkout_url, self.accounts.users, billing, user, site=self.accounts.site
        )
        url = await billing.run(paying)
        return Reply(PaymentPage(url=url), {}, HTTPStatus.OK)


@dataclass(frozen=True, slots=True, eq=False)
class BillingPortalController:
    """Sending a signed-in account to Stripe's portal: its card, its invoices, cancelling."""

    accounts: Accounts

    async def portal(self, user: User | None) -> Reply[PaymentPage]:
        """Where `user` manages what they pay: Stripe's portal, for the account's customer.

        Raises NoPayments, SignInToPay, or NeverPaid for an account that has never gone to
        pay; PaymentsUnavailable while Stripe can't be asked.
        """
        billing = _billing_of(self.accounts)
        if user is None:
            raise SignInToPay()
        if user.stripe_customer is None:
            raise NeverPaid()
        managing = partial(
            billing.stripe.portal_url, customer=user.stripe_customer, site=self.accounts.site
        )
        url = await billing.run(managing)
        return Reply(PaymentPage(url=url), {}, HTTPStatus.OK)


def _billing_of(accounts: Accounts) -> Billing:
    """How the server takes payment; NoPayments where it takes none."""
    if accounts.billing is None:
        raise NoPayments()
    return accounts.billing


def _checkout_url(users: UserStore, billing: Billing, user: User, *, site: str) -> str:
    """Stripe's checkout for `user`'s customer, made first where it has none."""
    customer = user.stripe_customer or _customer_of(users, billing, user)
    return billing.stripe.checkout_url(
        customer=customer, account=user.id, price=billing.price, site=site
    )


def _customer_of(users: UserStore, billing: Billing, user: User) -> str:
    """The Stripe customer `user`'s account keeps: Stripe's new one, or one kept since.

    Raises SignInToPay for an account removed while it went to pay.
    """
    made = billing.stripe.new_customer(account=user.id, email=user.email)
    kept = users.keep_customer(user.id, made)
    if kept is None:
        raise SignInToPay()
    return kept
