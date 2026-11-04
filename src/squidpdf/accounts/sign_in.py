"""Signing in: a link sent by email, then the link's token traded for a signed-in cookie.

No web framework here. Nothing is stored but the account: the link and the cookie are
each a signed value with a time limit (`signed.py`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from http import HTTPStatus

from squidpdf.accounts import constants
from squidpdf.accounts.cookies import SIGNED_IN, cookie_header
from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import LinkExpired, NotInvited
from squidpdf.accounts.mail import Letter
from squidpdf.accounts.signed import signed, value_of
from squidpdf.accounts.types import AccountState, LinkSent, SignedIn
from squidpdf.accounts.users import User, checked_email
from squidpdf.core import Message, Reply, words

_LINK_PATH = "/sign-in#"  # the browser's page that trades a link's token, kept out of logs
_SECONDS_A_MINUTE = 60


@dataclass(frozen=True, slots=True, eq=False)
class LinkController:
    """Sending a sign-in link, from the address typed to the sentence saying it's sent."""

    accounts: Accounts

    async def send(self, typed: str, *, language: str) -> Reply[LinkSent]:
        """Email a link that signs in as `typed`, good for LINK_VALID_S; say where it went.

        Invite-only, an address no one invited gets nothing, and is told what an invited one
        is: whether someone is invited is no one else's to find out.
        """
        email = checked_email(typed)
        said = "link_sent_if_invited" if self.accounts.invite_only else "link_sent"
        sentence = words.render(
            Message(said, {"email": email, "minutes": _link_minutes()}), language
        )
        reply = Reply(
            LinkSent(sentence=sentence), words.language_headers(language), HTTPStatus.ACCEPTED
        )
        if self.accounts.invite_only:
            invited = await asyncio.to_thread(self.accounts.users.find, email)
            # No one invited them: nothing is sent, and the reply is what the invited get.
            if invited is None:
                return reply
        # Off the server's thread: a mail server can take seconds to answer.
        await asyncio.to_thread(self.accounts.mailer.send, self._letter_to(email, language))
        return reply

    def _letter_to(self, email: str, language: str) -> Letter:
        """The email carrying a link that signs in as `email` until LINK_VALID_S from now."""
        until = int(time.time()) + constants.LINK_VALID_S
        token = signed(email, purpose="link", until=until, secret=self.accounts.secret)
        link = f"{self.accounts.site}{_LINK_PATH}{token}"
        return Letter(
            email,
            subject=words.render(Message("sign_in_subject"), language),
            body=words.render(
                Message("sign_in_letter", {"link": link, "minutes": _link_minutes()}), language
            ),
        )


@dataclass(frozen=True, slots=True, eq=False)
class SignInController:
    """Trading a link's token for the signed-in cookie, and the account it signs in to."""

    accounts: Accounts

    async def sign_in(self, token: str) -> Reply[AccountState]:
        """Sign in as the email `token` was signed for, making its account on first use.

        Raises LinkExpired for a token past its time, altered, or not a link's; NotInvited,
        invite-only, for an address no one invited.
        """
        now = int(time.time())
        email = value_of(token, purpose="link", now=now, secret=self.accounts.secret)
        if email is None:
            raise LinkExpired()
        user = await asyncio.to_thread(self._user_signing_in, email, now=now)
        until = now + constants.SIGNED_IN_S
        cookie = signed(user.id, purpose="signed_in", until=until, secret=self.accounts.secret)
        headers = {
            "Set-Cookie": cookie_header(SIGNED_IN, cookie, max_age=constants.SIGNED_IN_S)
        }
        return Reply(state_of(self.accounts, user), headers, HTTPStatus.OK)

    def _user_signing_in(self, email: str, *, now: int) -> User:
        """The account signing in as `email`: made on first use, unless only the invited may."""
        if not self.accounts.invite_only:
            return self.accounts.users.joined(email, now=now)
        invited = self.accounts.users.find(email)
        # A link sent while anyone could sign in.
        if invited is None:
            raise NotInvited()
        return invited


def _link_minutes() -> int:
    """How long a link works, in the minutes its email and its reply say."""
    return constants.LINK_VALID_S // _SECONDS_A_MINUTE


def signed_in_user(accounts: Accounts, cookie: str | None) -> User | None:
    """The account a signed-in cookie names; None with no cookie, a bad one, or no account.

    Reads the table: a coroutine calls it in a thread.
    """
    user_id = _signed_in_id(accounts, cookie)
    if user_id is None:
        return None
    return accounts.users.get(user_id)


def _signed_in_id(accounts: Accounts, cookie: str | None) -> str | None:
    """The account id a signed-in cookie names, by its signature alone; None if none holds."""
    if cookie is None:
        return None
    now = int(time.time())
    return value_of(cookie, purpose="signed_in", now=now, secret=accounts.secret)


def state_of(accounts: Accounts, user: User | None) -> AccountState:
    """What the browser is told of an account on a server that offers them."""
    signed_in = (
        None if user is None else SignedIn(email=user.email, is_unlimited=user.is_unlimited)
    )
    return AccountState(offered=True, invite_only=accounts.invite_only, signed_in=signed_in)


def signed_out(accounts: Accounts) -> Reply[AccountState]:
    """The reply to signing out: the cookie cleared, and no account."""
    headers = {"Set-Cookie": cookie_header(SIGNED_IN, "", max_age=0)}
    return Reply(state_of(accounts, None), headers, HTTPStatus.OK)
