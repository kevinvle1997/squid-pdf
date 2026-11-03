"""Accounts over HTTP: asking for a sign-in link, signing in with it, and signing out.

Routes are HTTP only: read the request, call the action's controller, send what it hands
back. Reading the account and signing out have none: each is one look at the cookie.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import NoAccounts
from squidpdf.accounts.sign_in import (
    SIGNED_IN_COOKIE,
    LinkController,
    SignInController,
    signed_in_user,
    signed_out,
    state_of,
)
from squidpdf.accounts.types import AccountState, LinkSent
from squidpdf.accounts.users import User
from squidpdf.api import rate
from squidpdf.api.language import ReaderLanguage
from squidpdf.api.routing import response_of

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


async def signed_in(
    request: Request, accounts: Annotated[Accounts | None, Depends(accounts_of)]
) -> User | None:
    """The account this browser is signed in to; None if none, or the server keeps none."""
    if accounts is None:
        return None
    # Off the loop: it reads the table.
    return await asyncio.to_thread(
        signed_in_user, accounts, request.cookies.get(SIGNED_IN_COOKIE)
    )


@router.get("")
async def account(
    accounts: Annotated[Accounts | None, Depends(accounts_of)],
    user: Annotated[User | None, Depends(signed_in)],
) -> AccountState:
    """Whether this server offers accounts, and which this browser is signed in to."""
    if accounts is None:
        return AccountState(offered=False, signed_in=None)
    return state_of(user)


@router.post(
    "/link", status_code=202, response_model=LinkSent, dependencies=[Depends(rate.admit_link)]
)
async def send_link(
    body: LinkBody,
    accounts: Annotated[Accounts, Depends(offered_accounts)],
    language: ReaderLanguage,
) -> Response:
    """Email a link that signs this browser in as `email`; say where it went."""
    reply = await LinkController(accounts).send(body.email, language=language)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-in", response_model=AccountState)
async def sign_in(
    body: SignInBody, accounts: Annotated[Accounts, Depends(offered_accounts)]
) -> Response:
    """Sign this browser in with a link's token, its account made on first use."""
    reply = await SignInController(accounts).sign_in(body.token)
    return response_of(reply, media_type=_JSON)


@router.post("/sign-out", response_model=AccountState, dependencies=[Depends(offered_accounts)])
async def sign_out() -> Response:
    """Sign this browser out: its signed-in cookie cleared."""
    return response_of(signed_out(), media_type=_JSON)
