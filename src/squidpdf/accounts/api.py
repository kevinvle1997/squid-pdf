"""Accounts over HTTP: a sign-in link asked for and used, signing out, and two webhooks.

Routes are HTTP only: read the request, call the action's controller, send what it hands
back. Reading the account and signing out have none: each is one look at the cookie. Also
`admit_invited`, the gate every route that works on a document goes through.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from squidpdf.accounts.billing.payment_pages import BillingPortalController, CheckoutController
from squidpdf.accounts.billing.webhook import BillingEventController
from squidpdf.accounts.bounces import BounceController, is_brevo
from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import InvitedOnly, NoAccounts
from squidpdf.accounts.sign_in import (
    LinkController,
    SignInController,
    signed_in_user,
    signed_out,
    state_of,
)
from squidpdf.accounts.types import AccountState, LinkSent, MailEvent, PaymentPage
from squidpdf.accounts.users import User, checked_email
from squidpdf.api import constants as limits, rate
from squidpdf.api.errors import RateLimited
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.rate import RecentStarts
from squidpdf.api.routing import response_of
from squidpdf.core import NotFound

router = APIRouter(prefix="/api/account")

_JSON = "application/json"


class LinkBody(BaseModel):
    """Where to send a sign-in link: the address as it was typed."""

    email: str


class SignInBody(BaseModel):
    """The token a sign-in link carried, as the browser read it from the link."""

    token: str


def _accounts_of(request: Request) -> Accounts | None:
    """The accounts the app read as it started; None on a server that keeps none."""
    return request.app.state.accounts


def _offered_accounts(accounts: Annotated[Accounts | None, Depends(_accounts_of)]) -> Accounts:
    """The app's accounts, or no_accounts on a server that keeps none."""
    if accounts is None:
        raise NoAccounts()
    return accounts


def _controller_with_accounts[C](cls: Callable[[Accounts], C]) -> Callable[[Accounts], C]:
    """A route's dependency: a new `cls` for each request, with the app's accounts.

    Used as `Depends(_controller_with_accounts(LinkController))`, as a document's routes use
    `controller_with_workers`; no_accounts on a server that keeps none.
    """

    def with_the_apps_accounts(accounts: Annotated[Accounts, Depends(_offered_accounts)]) -> C:
        """The controller, built with the app's accounts."""
        return cls(accounts)

    return with_the_apps_accounts


async def signed_in(
    request: Request, accounts: Annotated[Accounts | None, Depends(_accounts_of)]
) -> User | None:
    """The account this browser is signed in to; None if none, or the server keeps none."""
    if accounts is None:
        return None
    # Off the loop: it reads the table.
    return await asyncio.to_thread(signed_in_user, accounts, request.cookies)


async def admit_invited(
    request: Request, accounts: Annotated[Accounts | None, Depends(_accounts_of)]
) -> None:
    """Raise invited_only unless this browser is signed in, where only the invited may work.

    On every route that works on a document, before any body is read. It asks the table,
    so an account `squidpdf remove` took off is let go from every browser at once.
    """
    if accounts is None or not accounts.invite_only:
        return
    # Off the loop: it reads the table.
    user = await asyncio.to_thread(signed_in_user, accounts, request.cookies)
    if user is None:
        raise InvitedOnly()


def _link_body(body: LinkBody) -> LinkBody:
    """The request's body, read once: the route and the gate on its inbox both take it here."""
    return body


async def _admit_link_to(
    request: Request, body: Annotated[LinkBody, Depends(_link_body)]
) -> None:
    """Raise rate_limited if the inbox was sent LINKS_PER_INBOX links while one still works.

    From every address together, after `rate.admit_link` counts this one: a flood of
    addresses can't fill one inbox from our sender. Async, so two asked at once never count
    as one. The address is checked first, so what can't be one counts nothing.
    """
    recent_links_to: RecentStarts = request.app.state.recent_links_to  # made in the lifespan
    # Read as a module attribute, so a test can change the limit.
    admitted = recent_links_to.admit(
        checked_email(body.email), now=time.monotonic(), limit=limits.LINKS_PER_INBOX
    )
    if not admitted:
        raise RateLimited()


def _admit_brevo(
    accounts: Annotated[Accounts, Depends(_offered_accounts)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Raise not_found unless this is Brevo's webhook, with its token.

    Asked before the body is checked, so a stranger's is never read.
    """
    if not is_brevo(accounts, authorization):
        raise NotFound()


@router.get("")
async def account(
    accounts: Annotated[Accounts | None, Depends(_accounts_of)],
    user: Annotated[User | None, Depends(signed_in)],
) -> AccountState:
    """Whether this server offers accounts, and which this browser is signed in to."""
    # Off the loop: it reads the table for the month's documents.
    return await asyncio.to_thread(state_of, accounts, user)


@router.post(
    "/link",
    status_code=202,
    response_model=LinkSent,
    dependencies=[Depends(rate.admit_link), Depends(_admit_link_to)],
)
async def send_link(
    body: Annotated[LinkBody, Depends(_link_body)],
    link_controller: Annotated[
        LinkController, Depends(_controller_with_accounts(LinkController))
    ],
    language: ReaderLanguage,
) -> Response:
    """Email a link that signs this browser in as `email`; say where it went."""
    reply = await link_controller.send(body.email, language=language)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-in", response_model=AccountState)
async def sign_in(
    body: SignInBody,
    sign_in_controller: Annotated[
        SignInController, Depends(_controller_with_accounts(SignInController))
    ],
) -> Response:
    """Sign this browser in with a link's token, its account made on first use."""
    reply = await sign_in_controller.sign_in(body.token)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-out", response_model=AccountState)
async def sign_out(accounts: Annotated[Accounts, Depends(_offered_accounts)]) -> Response:
    """Sign this browser out: its signed-in cookie cleared."""
    return response_of(signed_out(accounts), media_type=_JSON)


@router.post("/mail-events", status_code=204, dependencies=[Depends(_admit_brevo)])
async def mail_events(
    body: MailEvent | list[MailEvent],
    bounce_controller: Annotated[
        BounceController, Depends(_controller_with_accounts(BounceController))
    ],
) -> Response:
    """Brevo's webhook: note each address mail can never reach."""
    return response_of(await bounce_controller.note(body), media_type=_JSON)


@router.post(
    "/checkout", response_model=PaymentPage, dependencies=[Depends(rate.admit_payment_page)]
)
async def checkout(
    checkout_controller: Annotated[
        CheckoutController, Depends(_controller_with_accounts(CheckoutController))
    ],
    user: Annotated[User | None, Depends(signed_in)],
) -> Response:
    """Where this browser's account pays monthly for no document limit: Stripe's checkout."""
    return response_of(await checkout_controller.checkout(user), media_type=_JSON)


@router.post(
    "/billing-portal",
    response_model=PaymentPage,
    dependencies=[Depends(rate.admit_payment_page)],
)
async def billing_portal(
    billing_portal_controller: Annotated[
        BillingPortalController, Depends(_controller_with_accounts(BillingPortalController))
    ],
    user: Annotated[User | None, Depends(signed_in)],
) -> Response:
    """Where this browser's account manages what it pays: Stripe's portal."""
    return response_of(await billing_portal_controller.portal(user), media_type=_JSON)


@router.post("/billing-events", status_code=204)
async def billing_events(
    request: Request,
    billing_event_controller: Annotated[
        BillingEventController, Depends(_controller_with_accounts(BillingEventController))
    ],
    stripe_signature: Annotated[str | None, Header()] = None,
) -> Response:
    """Stripe's webhook: keep each event once, and set the paid time it leads to.

    Its body is read as the bytes Stripe signed: read as JSON first, no signature holds.
    """
    payload = await request.body()
    reply = await billing_event_controller.note(payload, signature=stripe_signature)
    return response_of(reply, media_type=_JSON)
