"""Accounts over HTTP: a sign-in link asked for and used, signing out, and Brevo's bounces.

Routes are HTTP only: read the request, call the action's controller, send what it hands
back. Reading the account and signing out have none: each is one look at the cookie. Also
`admit_invited`, the gate every route that works on a document goes through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from squidpdf.accounts.bounces import BounceController, is_brevo
from squidpdf.accounts.cookies import SIGNED_IN
from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import InvitedOnly, NoAccounts
from squidpdf.accounts.sign_in import (
    LinkController,
    SignInController,
    signed_in_user,
    signed_out,
    state_of,
)
from squidpdf.accounts.types import AccountState, LinkSent, MailEvent
from squidpdf.accounts.users import User
from squidpdf.api import rate
from squidpdf.api.language import ReaderLanguage
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


def accounts_of(request: Request) -> Accounts | None:
    """The accounts the app read as it started; None on a server that keeps none."""
    return request.app.state.accounts


def offered_accounts(accounts: Annotated[Accounts | None, Depends(accounts_of)]) -> Accounts:
    """The app's accounts, or no_accounts on a server that keeps none."""
    if accounts is None:
        raise NoAccounts()
    return accounts


def controller_with_accounts[C](cls: Callable[[Accounts], C]) -> Callable[[Accounts], C]:
    """A route's dependency: a new `cls` for each request, with the app's accounts.

    Used as `Depends(controller_with_accounts(LinkController))`, as a document's routes use
    `controller_with_workers`; no_accounts on a server that keeps none.
    """

    def with_the_apps_accounts(accounts: Annotated[Accounts, Depends(offered_accounts)]) -> C:
        """The controller, built with the app's accounts."""
        return cls(accounts)

    return with_the_apps_accounts


async def signed_in(
    request: Request, accounts: Annotated[Accounts | None, Depends(accounts_of)]
) -> User | None:
    """The account this browser is signed in to; None if none, or the server keeps none."""
    if accounts is None:
        return None
    # Off the loop: it reads the table. .get: a browser that never signed in has no cookie.
    return await asyncio.to_thread(signed_in_user, accounts, request.cookies.get(SIGNED_IN))


async def admit_invited(
    request: Request, accounts: Annotated[Accounts | None, Depends(accounts_of)]
) -> None:
    """Raise invited_only unless this browser is signed in, where only the invited may work.

    On every route that works on a document, before any body is read. It asks the table,
    so an account `squidpdf remove` took off is let go from every browser at once.
    """
    if accounts is None or not accounts.invite_only:
        return
    # Off the loop: it reads the table. .get: a browser that never signed in has no cookie.
    user = await asyncio.to_thread(signed_in_user, accounts, request.cookies.get(SIGNED_IN))
    if user is None:
        raise InvitedOnly()


def admit_brevo(
    accounts: Annotated[Accounts, Depends(offered_accounts)],
    authorization: Annotated[str | None, Header()] = None,
) -> Accounts:
    """The app's accounts, or not_found unless this is Brevo's webhook, with its token.

    Asked before the body is checked, so a stranger's is never read.
    """
    if not is_brevo(accounts, authorization):
        raise NotFound()
    return accounts


@router.get("")
async def account(
    accounts: Annotated[Accounts | None, Depends(accounts_of)],
    user: Annotated[User | None, Depends(signed_in)],
) -> AccountState:
    """Whether this server offers accounts, and which this browser is signed in to."""
    if accounts is None:
        return AccountState(offered=False, invite_only=False, signed_in=None)
    return state_of(accounts, user)


@router.post(
    "/link", status_code=202, response_model=LinkSent, dependencies=[Depends(rate.admit_link)]
)
async def send_link(
    body: LinkBody,
    link_controller: Annotated[
        LinkController, Depends(controller_with_accounts(LinkController))
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
        SignInController, Depends(controller_with_accounts(SignInController))
    ],
) -> Response:
    """Sign this browser in with a link's token, its account made on first use."""
    reply = await sign_in_controller.sign_in(body.token)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-out", response_model=AccountState)
async def sign_out(accounts: Annotated[Accounts, Depends(offered_accounts)]) -> Response:
    """Sign this browser out: its signed-in cookie cleared."""
    return response_of(signed_out(accounts), media_type=_JSON)


@router.post("/mail-events", status_code=204)
async def mail_events(
    # Brevo sends one event, or a list where its webhook batches them.
    body: MailEvent | list[MailEvent],
    accounts: Annotated[Accounts, Depends(admit_brevo)],
) -> Response:
    """Brevo's webhook: note each address mail can never reach."""
    events = body if isinstance(body, list) else [body]
    return response_of(await BounceController(accounts).note(events), media_type=_JSON)
