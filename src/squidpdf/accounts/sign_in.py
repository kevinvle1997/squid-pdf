"""Signing in: a link sent by email, then the link's token traded for a signed-in cookie.

No web framework here. Nothing is stored but the account: the link and the cookie are
each a signed value with a time limit (`signed.py`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie

from squidpdf.accounts import constants
from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import LinkExpired
from squidpdf.accounts.mail import Letter
from squidpdf.accounts.signed import signed, value_of
from squidpdf.accounts.types import AccountState, LinkSent, SignedIn
from squidpdf.accounts.users import User, checked_email
from squidpdf.core import Message, Reply, words

# The signed-in cookie. __Host-: only this host can set it, so no subdomain plants one.
SIGNED_IN_COOKIE = "__Host-signed-in"
_LINK_PATH = "/sign-in#"  # the browser's page that trades a link's token, kept out of logs
_SECONDS_A_MINUTE = 60


@dataclass(frozen=True, slots=True, eq=False)
class LinkController:
    """Sending a sign-in link, from the address typed to the sentence saying it's sent."""

    accounts: Accounts

    async def send(self, typed: str, *, language: str) -> Reply[LinkSent]:
        """Email a link that signs in as `typed`, good for LINK_VALID_S; say where it went."""
        email = checked_email(typed)
        until = int(time.time()) + constants.LINK_VALID_S
        token = signed(email, purpose="link", until=until, secret=self.accounts.secret)
        minutes = constants.LINK_VALID_S // _SECONDS_A_MINUTE
        letter = Letter(
            email,
            subject=words.render(Message("sign_in_subject"), language),
            body=words.render(
                Message(
                    "sign_in_letter",
                    {"link": f"{self.accounts.site}{_LINK_PATH}{token}", "minutes": minutes},
                ),
                language,
            ),
        )
        # Off the server's thread: a mail server can take seconds to answer.
        await asyncio.to_thread(self.accounts.mailer.send, letter)
        sentence = words.render(
            Message("link_sent", {"email": email, "minutes": minutes}), language
        )
        return Reply(
            LinkSent(sentence=sentence), words.language_headers(language), HTTPStatus.ACCEPTED
        )


@dataclass(frozen=True, slots=True, eq=False)
class SignInController:
    """Trading a link's token for the signed-in cookie, and the account it signs in to."""

    accounts: Accounts

    async def sign_in(self, token: str) -> Reply[AccountState]:
        """Sign in as the email `token` was signed for, making its account on first use.

        Raises LinkExpired for a token past its time, altered, or not a link's.
        """
        now = int(time.time())
        email = value_of(token, purpose="link", now=now, secret=self.accounts.secret)
        if email is None:
            raise LinkExpired()
        user = await asyncio.to_thread(self.accounts.users.joined, email, now=now)
        until = now + constants.SIGNED_IN_S
        cookie = signed(user.id, purpose="signed_in", until=until, secret=self.accounts.secret)
        headers = {"Set-Cookie": _cookie_header(cookie, max_age=constants.SIGNED_IN_S)}
        return Reply(state_of(user), headers, HTTPStatus.OK)


def signed_in_user(accounts: Accounts, cookie: str | None) -> User | None:
    """The account a signed-in cookie names; None with no cookie, a bad one, or no account.

    Reads the table: a coroutine calls it in a thread.
    """
    if cookie is None:
        return None
    user_id = value_of(
        cookie, purpose="signed_in", now=int(time.time()), secret=accounts.secret
    )
    if user_id is None:
        return None
    return accounts.users.get(user_id)


def state_of(user: User | None) -> AccountState:
    """What the browser is told of an account on a server that offers them."""
    signed_in = (
        None if user is None else SignedIn(email=user.email, is_unlimited=user.is_unlimited)
    )
    return AccountState(offered=True, signed_in=signed_in)


def signed_out() -> Reply[AccountState]:
    """The reply to signing out: the cookie cleared, and no account."""
    headers = {"Set-Cookie": _cookie_header("", max_age=0)}
    return Reply(state_of(None), headers, HTTPStatus.OK)


def _cookie_header(value: str, *, max_age: int) -> str:
    """The Set-Cookie header for the signed-in cookie: only over HTTPS, never read by script.

    SameSite=Strict: another site's page never sends a request as the signed-in browser.
    """
    cookie: SimpleCookie = SimpleCookie()
    cookie[SIGNED_IN_COOKIE] = value
    kept = cookie[SIGNED_IN_COOKIE]
    kept["path"] = "/"
    kept["max-age"] = max_age
    kept["secure"] = True
    kept["httponly"] = True
    kept["samesite"] = "Strict"
    return kept.OutputString()
