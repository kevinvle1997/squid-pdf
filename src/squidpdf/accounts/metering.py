"""A file counts once, by its digest.

Without an account the count is a signed cookie, which clearing resets: a nudge, not a lock.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

from squidpdf.accounts import constants
from squidpdf.accounts.cookies import COUNTED, cookie_header
from squidpdf.accounts.deployment import Accounts, Allowance
from squidpdf.accounts.errors import FreeDocumentsUsed, MonthlyDocumentsUsed
from squidpdf.accounts.sign_in import signed_in_user
from squidpdf.accounts.signed import read_signed, signed
from squidpdf.accounts.users import User, this_month
from squidpdf.documents.meters import Meter


@dataclass(frozen=True, slots=True, eq=False)
class AccountMeters:
    accounts: Accounts
    allowance: Allowance

    def meter_for(self, cookies: Mapping[str, str]) -> Meter:
        user = signed_in_user(self.accounts, cookies)
        if user is not None:
            return _AccountMeter(self.accounts, self.allowance, user=user)
        return _BrowserMeter(self.accounts, self.allowance, counted=self._counted_in(cookies))

    def _counted_in(self, cookies: Mapping[str, str]) -> frozenset[str]:
        cookie = cookies.get(COUNTED)
        if cookie is None:
            return frozenset()
        now = int(time.time())
        listed = read_signed(cookie, purpose="counted", now=now, secret=self.accounts.secret)
        return frozenset() if listed is None else frozenset(listed.value.split())


@dataclass(frozen=True, slots=True, eq=False)
class _AccountMeter:
    accounts: Accounts
    allowance: Allowance
    user: User

    def check(self, digest: str) -> None:
        if self.user.has_no_limit_at(int(time.time())):
            return
        counted = self.accounts.users.counted_in(self.user.id, month=this_month())
        is_new = digest not in counted
        if is_new and len(counted) >= self.allowance.monthly:
            raise MonthlyDocumentsUsed(monthly=self.allowance.monthly)

    def count(self, digest: str) -> dict[str, str]:
        """The count is the gate: of two new files at once with one left, the second fails."""
        limit = None if self.user.has_no_limit_at(int(time.time())) else self.allowance.monthly
        self.accounts.users.count(self.user.id, digest, month=this_month(), limit=limit)
        return {}


@dataclass(frozen=True, slots=True, eq=False)
class _BrowserMeter:
    accounts: Accounts
    allowance: Allowance
    counted: frozenset[str]

    def check(self, digest: str) -> None:
        is_new = digest not in self.counted
        if is_new and len(self.counted) >= self.allowance.free:
            raise FreeDocumentsUsed(monthly=self.allowance.monthly)

    def count(self, digest: str) -> dict[str, str]:
        if digest in self.counted:
            return {}
        listed = " ".join(sorted(self.counted | {digest}))
        until = int(time.time()) + constants.COUNTED_KEPT_S
        cookie = signed(listed, purpose="counted", until=until, secret=self.accounts.secret)
        return {"Set-Cookie": cookie_header(COUNTED, cookie, max_age=constants.COUNTED_KEPT_S)}
