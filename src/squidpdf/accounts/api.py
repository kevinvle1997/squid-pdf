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
    email: str


class SignInBody(BaseModel):
    token: str


def _accounts_of(request: Request) -> Accounts | None:
    return request.app.state.accounts


def _offered_accounts(accounts: Annotated[Accounts | None, Depends(_accounts_of)]) -> Accounts:
    if accounts is None:
        raise NoAccounts()
    return accounts


def _controller_with_accounts[C](cls: Callable[[Accounts], C]) -> Callable[[Accounts], C]:
    def with_the_apps_accounts(accounts: Annotated[Accounts, Depends(_offered_accounts)]) -> C:
        return cls(accounts)

    return with_the_apps_accounts


async def signed_in(
    request: Request, accounts: Annotated[Accounts | None, Depends(_accounts_of)]
) -> User | None:
    if accounts is None:
        return None
    return await asyncio.to_thread(signed_in_user, accounts, request.cookies)


async def admit_invited(
    request: Request, accounts: Annotated[Accounts | None, Depends(_accounts_of)]
) -> None:
    if accounts is None or not accounts.invite_only:
        return
    user = await asyncio.to_thread(signed_in_user, accounts, request.cookies)
    if user is None:
        raise InvitedOnly()


def _link_body(body: LinkBody) -> LinkBody:
    return body


async def _admit_link_to(
    request: Request, body: Annotated[LinkBody, Depends(_link_body)]
) -> None:
    """Async, so two at once never count as one; per inbox, so no flood fills one."""
    recent_links_to: RecentStarts = request.app.state.recent_links_to
    admitted = recent_links_to.admit(
        checked_email(body.email), now=time.monotonic(), limit=limits.LINKS_PER_INBOX
    )
    if not admitted:
        raise RateLimited()


def _admit_brevo(
    accounts: Annotated[Accounts, Depends(_offered_accounts)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Asked before the body is checked, so a stranger's is never read."""
    if not is_brevo(accounts, authorization):
        raise NotFound()


@router.get("")
async def account(
    accounts: Annotated[Accounts | None, Depends(_accounts_of)],
    user: Annotated[User | None, Depends(signed_in)],
) -> AccountState:
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
    reply = await link_controller.send(body.email, language=language)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-in", response_model=AccountState)
async def sign_in(
    body: SignInBody,
    sign_in_controller: Annotated[
        SignInController, Depends(_controller_with_accounts(SignInController))
    ],
) -> Response:
    reply = await sign_in_controller.sign_in(body.token)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-out", response_model=AccountState)
async def sign_out(accounts: Annotated[Accounts, Depends(_offered_accounts)]) -> Response:
    return response_of(signed_out(accounts), media_type=_JSON)


@router.post("/mail-events", status_code=204, dependencies=[Depends(_admit_brevo)])
async def mail_events(
    body: MailEvent | list[MailEvent],
    bounce_controller: Annotated[
        BounceController, Depends(_controller_with_accounts(BounceController))
    ],
) -> Response:
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
    return response_of(await billing_portal_controller.portal(user), media_type=_JSON)


@router.post("/billing-events", status_code=204)
async def billing_events(
    request: Request,
    billing_event_controller: Annotated[
        BillingEventController, Depends(_controller_with_accounts(BillingEventController))
    ],
    stripe_signature: Annotated[str | None, Header()] = None,
) -> Response:
    """Read as the bytes Stripe signed: parsed as JSON first, no signature holds."""
    payload = await request.body()
    reply = await billing_event_controller.note(payload, signature=stripe_signature)
    return response_of(reply, media_type=_JSON)
