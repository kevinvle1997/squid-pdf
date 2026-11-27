from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus

from squidpdf.accounts.billing.stripe_api import NotAtStripe
from squidpdf.accounts.deployment import Accounts, Billing
from squidpdf.accounts.errors import (
    AlreadySubscribed,
    AlreadyUnlimited,
    NoBilling,
    NoPayments,
    SignInToPay,
)
from squidpdf.accounts.types import PaymentPage
from squidpdf.accounts.users import User, UserStore
from squidpdf.core import Reply


@dataclass(frozen=True, slots=True, eq=False)
class CheckoutController:
    accounts: Accounts

    async def checkout(self, user: User | None) -> Reply[PaymentPage]:
        billing = _billing_of(self.accounts)
        if user is None:
            raise SignInToPay()
        if user.has_no_limit_at(int(time.time())):
            raise AlreadyUnlimited()
        paying = partial(
            _checkout_url, self.accounts.users, billing, user, site=self.accounts.site
        )
        url = await billing.run(paying)
        return Reply(PaymentPage(url=url), {}, HTTPStatus.OK)


@dataclass(frozen=True, slots=True, eq=False)
class BillingPortalController:
    accounts: Accounts

    async def portal(self, user: User | None) -> Reply[PaymentPage]:
        billing = _billing_of(self.accounts)
        if user is None:
            raise SignInToPay()
        if user.stripe_customer is None:
            raise NoBilling()
        managing = partial(
            _portal_url,
            self.accounts.users,
            billing,
            user,
            customer=user.stripe_customer,
            site=self.accounts.site,
        )
        url = await billing.run(managing)
        return Reply(PaymentPage(url=url), {}, HTTPStatus.OK)


def _billing_of(accounts: Accounts) -> Billing:
    if accounts.billing is None:
        raise NoPayments()
    return accounts.billing


def _checkout_url(users: UserStore, billing: Billing, user: User, *, site: str) -> str:
    """Asks Stripe first: paid time here can be late, and a second subscription pays twice."""
    customer = user.stripe_customer
    if customer is None:
        return _checkout_for(
            billing, user, customer=_customer_of(users, billing, user), site=site
        )
    if billing.stripe.subscriptions(customer, account=user.id).unended > 0:
        raise AlreadySubscribed()
    return _checkout_as_kept(users, billing, user, customer=customer, site=site)


def _checkout_as_kept(
    users: UserStore, billing: Billing, user: User, *, customer: str, site: str
) -> str:
    try:
        return _checkout_for(billing, user, customer=customer, site=site)
    except NotAtStripe:
        users.forget_customer(user.id, customer)
    return _checkout_for(billing, user, customer=_customer_of(users, billing, user), site=site)


def _checkout_for(billing: Billing, user: User, *, customer: str, site: str) -> str:
    return billing.stripe.checkout_url(
        customer=customer, account=user.id, price=billing.price, site=site
    )


def _portal_url(
    users: UserStore, billing: Billing, user: User, *, customer: str, site: str
) -> str:
    try:
        return billing.stripe.billing_portal_url(customer=customer, site=site)
    except NotAtStripe:
        users.forget_customer(user.id, customer)
        raise NoBilling() from None


def _customer_of(users: UserStore, billing: Billing, user: User) -> str:
    made = billing.stripe.new_customer(account=user.id, email=user.email)
    kept = users.keep_customer(user.id, made)
    if kept is None:
        raise SignInToPay()
    return kept
