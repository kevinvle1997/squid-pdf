"""How many documents a browser opens: some without an account, then some a month with one.

A file counts once, by its digest, however often it's opened: a browser reopening its own
document after the hour isn't charged again. Without an account the count is a signed
cookie, which a cleared cookie resets: a nudge to sign in, not a lock.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from squidpdf.accounts import constants
from squidpdf.accounts.cookies import COUNTED, SIGNED_IN, cookie_header
from squidpdf.accounts.deployment import Accounts, Allowance
from squidpdf.accounts.errors import FreeDocumentsUsed, MonthlyDocumentsUsed
from squidpdf.accounts.sign_in import signed_in_id
from squidpdf.accounts.signed import signed, value_of
from squidpdf.documents.meters import Meter


@dataclass(frozen=True, slots=True, eq=False)
class AccountMeters:
    """Each browser's meter, on a server whose deployment sets an allowance."""

    accounts: Accounts
    allowance: Allowance

    def meter_for(self, cookies: Mapping[str, str]) -> Meter:
        """The meter of the browser that sent `cookies`: its account's, or its own."""
        user_id = signed_in_id(self.accounts, cookies.get(SIGNED_IN))
        if user_id is not None:
            return _AccountMeter(self.accounts, self.allowance, user_id=user_id)
        return _BrowserMeter(self.accounts, self.allowance, counted=self._counted_in(cookies))

    def _counted_in(self, cookies: Mapping[str, str]) -> frozenset[str]:
        """The files this browser opened without an account, by the cookie that lists them."""
        cookie = cookies.get(COUNTED)  # None on a browser that has opened none
        if cookie is None:
            return frozenset()
        now = int(time.time())
        listed = value_of(cookie, purpose="counted", now=now, secret=self.accounts.secret)
        return frozenset() if listed is None else frozenset(listed.split())


@dataclass(frozen=True, slots=True, eq=False)
class _AccountMeter:
    """A signed-in browser's meter: its account's documents this month, or none if unlimited."""

    accounts: Accounts
    allowance: Allowance
    user_id: str

    def check(self, digest: str) -> None:
        """Raise MonthlyDocumentsUsed if `digest` is new and the month's are used."""
        if self.accounts.users.is_unlimited(self.user_id):
            return
        counted = self.accounts.users.counted_in(self.user_id, month=_this_month())
        is_new = digest not in counted
        if is_new and len(counted) >= self.allowance.monthly:
            raise MonthlyDocumentsUsed(monthly=self.allowance.monthly)

    def count(self, digest: str) -> dict[str, str]:
        """Count `digest` against this month's, kept in the table: no header needed."""
        self.accounts.users.count(self.user_id, digest, month=_this_month())
        return {}


@dataclass(frozen=True, slots=True, eq=False)
class _BrowserMeter:
    """A browser's meter with no account: the documents it may open before it signs in."""

    accounts: Accounts
    allowance: Allowance
    counted: frozenset[str]  # the files it has opened, by digest

    def check(self, digest: str) -> None:
        """Raise FreeDocumentsUsed if `digest` is new and the free ones are used."""
        is_new = digest not in self.counted
        if is_new and len(self.counted) >= self.allowance.free:
            raise FreeDocumentsUsed(monthly=self.allowance.monthly)

    def count(self, digest: str) -> dict[str, str]:
        """Count `digest` in the browser's own cookie: the header that keeps it."""
        if digest in self.counted:
            return {}
        listed = " ".join(sorted(self.counted | {digest}))
        until = int(time.time()) + constants.COUNTED_KEPT_S
        cookie = signed(listed, purpose="counted", until=until, secret=self.accounts.secret)
        return {"Set-Cookie": cookie_header(COUNTED, cookie, max_age=constants.COUNTED_KEPT_S)}


def _this_month() -> str:
    """The month a count is for, as "2026-11", in UTC: the same for every server."""
    return datetime.now(UTC).strftime("%Y-%m")
