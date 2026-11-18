"""Paying for no document limit: what Stripe says is paid, kept in a ledger, lifts the limit."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.accounts import constants
from squidpdf.accounts.cookies import SIGNED_IN
from squidpdf.accounts.deployment import Accounts, Allowance
from squidpdf.accounts.mail import Letter, MailController
from squidpdf.accounts.metering import AccountMeters
from squidpdf.accounts.signed import signed
from squidpdf.accounts.types import BillingEvent
from squidpdf.accounts.users import UserStore, open_users
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_problem

_SECRET = b"a test's secret, long enough to sign with"
_EMAIL = "payer@example.com"


class _NoMail:
    """A mailer for tests that send none."""

    def send(self, letter: Letter) -> None:
        raise AssertionError(f"no email in these tests, but one to {letter.to}")


@pytest.fixture
def users(app: FastAPI, tmp_path, monkeypatch) -> UserStore:
    """The app keeping accounts in a fresh table, an account opening one document a month."""
    allowance = Allowance(free=1, monthly=1)
    store = open_users(tmp_path / "users.sqlite3")
    accounts = Accounts(
        store,
        MailController(_NoMail(), store),
        secret=_SECRET,
        site="https://pdf.example.com",
        invite_only=False,
        allowance=allowance,
        webhook_token=None,
    )
    monkeypatch.setattr(app.state, "accounts", accounts)
    monkeypatch.setattr(app.state, "meters", AccountMeters(accounts, allowance))
    return store


def _signed_in_as(reader: TestClient, users: UserStore, email: str = _EMAIL) -> str:
    """Sign `reader` in to the account under `email`, made if need be; its id."""
    user = users.joined(email, now=0)
    cookie = signed(user.id, purpose="signed_in", until=2**40, secret=_SECRET)
    reader.cookies.set(SIGNED_IN, cookie)
    return user.id


def _paid(account: str, *, event: str, until: int | None) -> BillingEvent:
    """An event that set `account`'s paid time to `until`, as Stripe's webhook would."""
    return BillingEvent(
        event,
        type="customer.subscription.updated",
        created=0,
        customer="cus_test",
        account=account,
        outcome="applied",
        paid_until=until,
    )


def _files(pdf_bytes: bytes, count: int) -> list[bytes]:
    """`count` different files that each open: the sample, a comment after its end apiece."""
    return [pdf_bytes + f"\n% file {number}\n".encode() for number in range(count)]


def test_a_paid_month_lifts_the_limit_until_paid_time_and_the_renewals_leeway_run_out(
    users, browser: Callable[[], TestClient], pdf_bytes
):
    reader = browser()
    account = _signed_in_as(reader, users)
    files = _files(pdf_bytes, 5)
    assert_equal(upload(reader, files[0]).status_code, 201, "the month's one")
    assert_problem(upload(reader, files[1]), "monthly_documents_used", 403)
    now = int(time.time())
    users.apply_billing(_paid(account, event="evt_paid", until=now + 3600), now=now)
    assert_equal(upload(reader, files[1]).status_code, 201, "paid: past the month's one")
    # The month ended a minute ago, and its renewal is still being charged.
    users.apply_billing(_paid(account, event="evt_renewing", until=now - 60), now=now)
    assert_equal(upload(reader, files[2]).status_code, 201, "within the renewal's leeway")
    ran_out = now - constants.RENEWAL_LEEWAY_S - 60
    users.apply_billing(_paid(account, event="evt_unpaid", until=ran_out), now=now)
    assert_problem(upload(reader, files[3]), "monthly_documents_used", 403)


def test_an_event_sent_again_changes_nothing_it_set_since(
    users, browser: Callable[[], TestClient], pdf_bytes
):
    """Stripe sends an event again until it hears back: the ledger knows it, and keeps now."""
    reader = browser()
    account = _signed_in_as(reader, users)
    files = _files(pdf_bytes, 2)
    upload(reader, files[0])
    now = int(time.time())
    paid = _paid(account, event="evt_paid", until=now + 3600)
    assert_equal(users.apply_billing(paid, now=now), True, "new")
    cancelled = _paid(account, event="evt_cancelled", until=None)
    assert_equal(users.apply_billing(cancelled, now=now), True, "new")
    assert_equal(users.apply_billing(paid, now=now), False, "the first, sent again")
    assert_problem(upload(reader, files[1]), "monthly_documents_used", 403)
