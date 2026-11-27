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

_LINK_PATH = "/sign-in#"
_SECONDS_A_MINUTE = 60


@dataclass(frozen=True, slots=True, eq=False)
class LinkController:
    accounts: Accounts

    async def send(self, typed: str, *, language: str) -> Reply[LinkSent]:
        """Invite-only, one not invited is told what an invited one is, so no one can tell."""
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
            await self.accounts.send_mail(sending)
            return reply
        invited = await asyncio.to_thread(self.accounts.users.find, email)
        if invited is None:
            return reply
        # Sent after the reply: waited for, the mailer's seconds would tell who is invited.
        self.accounts.send_mail_unawaited(partial(_send_unawaited, sending))
        return reply

    def _link_for(self, email: str) -> str:
        until = int(time.time()) + constants.LINK_VALID_S
        token = signed(email, purpose="link", until=until, secret=self.accounts.secret)
        return f"{self.accounts.site}{_LINK_PATH}{token}"


@dataclass(frozen=True, slots=True, eq=False)
class SignInController:
    accounts: Accounts

    async def sign_in(self, token: str) -> Reply[AccountState]:
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
        user = self._account_of(link.value, now=now)
        if not self.accounts.users.spend_link(link.value, until=link.until):
            raise LinkExpired()
        return user

    def _account_of(self, email: str, *, now: int) -> User:
        if not self.accounts.invite_only:
            return self.accounts.users.joined(email, now=now)
        invited = self.accounts.users.find(email)
        if invited is None:
            raise NotInvited()
        return invited


def _send_unawaited(sending: Callable[[], None]) -> None:
    try:
        sending()
    except (MailNotSent, MailLimitReached) as refused:
        _log.write(LogEvent.LINK_NOT_SENT_UNSAID, refused)
    except Exception as failed:  # noqa: BLE001 (raised by a bug: no reply is left to say it)
        _log.write(LogEvent.LINK_NOT_SENT_UNSAID, failed, level=ERROR)


def _link_minutes() -> int:
    return constants.LINK_VALID_S // _SECONDS_A_MINUTE


def signed_in_user(accounts: Accounts, cookies: Mapping[str, str]) -> User | None:
    cookie = cookies.get(SIGNED_IN)
    user_id = _signed_in_id(accounts, cookie)
    if user_id is None:
        return None
    return accounts.users.get(user_id)


def _signed_in_id(accounts: Accounts, cookie: str | None) -> str | None:
    if cookie is None:
        return None
    now = int(time.time())
    read = read_signed(cookie, purpose="signed_in", now=now, secret=accounts.secret)
    return None if read is None else read.value


def state_of(accounts: Accounts | None, user: User | None) -> AccountState:
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
    now = int(time.time())
    return SignedIn(
        email=user.email,
        is_unlimited=user.is_unlimited,
        paid_until=user.paid_until if user.is_paid_at(now) else None,
        has_billing=user.stripe_customer is not None,
        documents_left=_documents_left(accounts, user, now=now),
    )


def _documents_left(accounts: Accounts, user: User, *, now: int) -> DocumentsLeft | None:
    allowance = accounts.allowance
    if allowance is None or user.has_no_limit_at(now):
        return None
    opened = len(accounts.users.counted_in(user.id, month=this_month()))
    left = max(allowance.monthly - opened, 0)
    return DocumentsLeft(left=left, monthly=allowance.monthly)


def signed_out(accounts: Accounts) -> Reply[AccountState]:
    headers = {"Set-Cookie": cookie_header(SIGNED_IN, "", max_age=0)}
    return Reply(state_of(accounts, None), headers, HTTPStatus.OK)
