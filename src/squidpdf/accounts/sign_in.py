"""Signing in: a link sent by email, then the link's token traded for a signed-in cookie.

No web framework here. Nothing is stored but the account: the link and the cookie are
each a signed value with a time limit (`signed.py`), and the account notes the last link
used, so none signs in twice.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus

from squidpdf.accounts import constants
from squidpdf.accounts.cookies import SIGNED_IN, cookie_header
from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.errors import LinkExpired, MailLimitReached, MailNotSent, NotInvited
from squidpdf.accounts.signed import Signed, read_signed, signed
from squidpdf.accounts.types import AccountState, DocumentsLeft, LinkSent, SignedIn
from squidpdf.accounts.users import User, checked_email, this_month
from squidpdf.core import ERROR, LogController, LogEvent, Message, Reply, words

_log = LogController.for_module(__name__)

_LINK_PATH = "/sign-in#"  # the browser's page that trades a link's token, kept out of logs
_SECONDS_A_MINUTE = 60


@dataclass(frozen=True, slots=True, eq=False)
class LinkController:
    """Sending a sign-in link, from the address typed to the sentence saying it's sent."""

    accounts: Accounts

    async def send(self, typed: str, *, language: str) -> Reply[LinkSent]:
        """Email a link that signs in as `typed`, good for LINK_VALID_S; say where it went.

        Invite-only, an address no one invited gets nothing, and is told what an invited one
        is: whether someone is invited is no one else's to find out, by the words or the wait.
        """
        email = checked_email(typed)
        said = "link_sent_if_invited" if self.accounts.invite_only else "link_sent"
        sentence = words.render(
            Message(said, {"email": email, "minutes": _link_minutes()}), language
        )
        reply = Reply(
            LinkSent(sentence=sentence), words.language_headers(language), HTTPStatus.ACCEPTED
        )
        sending = partial(
            self.accounts.mail.send_sign_in_link,
            email,
            link=self._link_for(email),
            minutes=_link_minutes(),
            language=language,
        )
        if not self.accounts.invite_only:
            # On mail's own threads: a mailer can take seconds to answer, or hang.
            await self.accounts.send_mail(sending)
            return reply
        invited = await asyncio.to_thread(self.accounts.users.find, email)
        # No one invited them: nothing is sent, and the reply is what the invited get.
        if invited is None:
            return reply
        # Sent after the reply: waited for, the mailer's seconds would tell who is invited.
        self.accounts.send_mail_unawaited(partial(_send_unawaited, sending))
        return reply

    def _link_for(self, email: str) -> str:
        """The link that signs in as `email` until LINK_VALID_S from now."""
        until = int(time.time()) + constants.LINK_VALID_S
        token = signed(email, purpose="link", until=until, secret=self.accounts.secret)
        return f"{self.accounts.site}{_LINK_PATH}{token}"


@dataclass(frozen=True, slots=True, eq=False)
class SignInController:
    """Trading a link's token for the signed-in cookie, and the account it signs in to."""

    accounts: Accounts

    async def sign_in(self, token: str) -> Reply[AccountState]:
        """Sign in as the email `token` was signed for, making its account on first use.

        Raises LinkExpired for a token past its time, used already, altered, or not a link's;
        NotInvited, invite-only, for an address no one invited.
        """
        now = int(time.time())
        link = read_signed(token, purpose="link", now=now, secret=self.accounts.secret)
        if link is None:
            raise LinkExpired()
        user = await asyncio.to_thread(self._user_signing_in, link, now=now)
        until = now + constants.SIGNED_IN_S
        cookie = signed(user.id, purpose="signed_in", until=until, secret=self.accounts.secret)
        headers = {
            "Set-Cookie": cookie_header(SIGNED_IN, cookie, max_age=constants.SIGNED_IN_S)
        }
        state = await asyncio.to_thread(state_of, self.accounts, user)
        return Reply(state, headers, HTTPStatus.OK)

    def _user_signing_in(self, link: Signed, *, now: int) -> User:
        """The account `link` signs in to, the link used up; LinkExpired if it was already."""
        user = self._account_of(link.value, now=now)
        # Used already, by this browser or another the link reached: it signs no one in again.
        if not self.accounts.users.spend_link(link.value, until=link.until):
            raise LinkExpired()
        return user

    def _account_of(self, email: str, *, now: int) -> User:
        """The account under `email`: made on first use, unless only the invited may."""
        if not self.accounts.invite_only:
            return self.accounts.users.joined(email, now=now)
        invited = self.accounts.users.find(email)
        # A link sent while anyone could sign in.
        if invited is None:
            raise NotInvited()
        return invited


def _send_unawaited(sending: Callable[[], None]) -> None:
    """Send a link no reply waits for; why it wasn't sent is the log's alone to say."""
    try:
        sending()
    except (MailNotSent, MailLimitReached) as refused:  # raised by the mailer: it said no
        _log.write(LogEvent.LINK_NOT_SENT_UNSAID, refused)
    except Exception as failed:  # noqa: BLE001 (raised by a bug: no reply is left to say it)
        _log.write(LogEvent.LINK_NOT_SENT_UNSAID, failed, level=ERROR)


def _link_minutes() -> int:
    """How long a link works, in the minutes its email and its reply say."""
    return constants.LINK_VALID_S // _SECONDS_A_MINUTE


def signed_in_user(accounts: Accounts, cookies: Mapping[str, str]) -> User | None:
    """The account `cookies` are signed in to; None with no cookie, a bad one, or no account.

    Reads the table: a coroutine calls it in a thread.
    """
    cookie = cookies.get(SIGNED_IN)  # None on a browser that never signed in
    user_id = _signed_in_id(accounts, cookie)
    if user_id is None:
        return None
    return accounts.users.get(user_id)


def _signed_in_id(accounts: Accounts, cookie: str | None) -> str | None:
    """The account id a signed-in cookie names, by its signature alone; None if none holds."""
    if cookie is None:
        return None
    now = int(time.time())
    read = read_signed(cookie, purpose="signed_in", now=now, secret=accounts.secret)
    return None if read is None else read.value


def state_of(accounts: Accounts | None, user: User | None) -> AccountState:
    """What the browser is told of accounts; `accounts` None on a server that keeps none.

    Reads the table for one signed in: a coroutine calls it in a thread.
    """
    if accounts is None:
        return AccountState(
            offered=False, invite_only=False, takes_payment=False, signed_in=None
        )
    return AccountState(
        offered=True,
        invite_only=accounts.invite_only,
        takes_payment=accounts.billing is not None,
        signed_in=None if user is None else _signed_in_as(accounts, user),
    )


def _signed_in_as(accounts: Accounts, user: User) -> SignedIn:
    """What the browser is told of the account it's signed in to."""
    now = int(time.time())
    return SignedIn(
        email=user.email,
        is_unlimited=user.is_unlimited,
        paid_until=user.paid_until if user.is_paid_at(now) else None,
        has_billing=user.stripe_customer is not None,
        documents_left=_documents_left(accounts, user, now=now),
    )


def _documents_left(accounts: Accounts, user: User, *, now: int) -> DocumentsLeft | None:
    """How many of this month's documents `user` has left; None where no limit applies."""
    allowance = accounts.allowance
    if allowance is None or user.has_no_limit_at(now):
        return None
    opened = len(accounts.users.counted_in(user.id, month=this_month()))
    # Fewer a month than it opened already, where the deployment lowered the number since.
    left = max(allowance.monthly - opened, 0)
    return DocumentsLeft(left=left, monthly=allowance.monthly)


def signed_out(accounts: Accounts) -> Reply[AccountState]:
    """The reply to signing out: the cookie cleared, and no account."""
    headers = {"Set-Cookie": cookie_header(SIGNED_IN, "", max_age=0)}
    return Reply(state_of(accounts, None), headers, HTTPStatus.OK)
