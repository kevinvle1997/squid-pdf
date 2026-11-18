"""Paying for no document limit: what Stripe says is paid, kept in a ledger, lifts the limit."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.accounts import constants
from squidpdf.accounts.billing.events import resync
from squidpdf.accounts.billing.removal import removal_of
from squidpdf.accounts.billing.stripe_api import stripe_api
from squidpdf.accounts.cookies import SIGNED_IN
from squidpdf.accounts.deployment import Accounts, AccountsUnset, Allowance, Billing
from squidpdf.accounts.mail import Letter, MailController
from squidpdf.accounts.metering import AccountMeters
from squidpdf.accounts.signed import signed
from squidpdf.accounts.types import BillingEvent
from squidpdf.accounts.users import UserStore, open_users
from squidpdf.api.app import create_app
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_problem

_SECRET = b"a test's secret, long enough to sign with"
_EMAIL = "payer@example.com"
_WEBHOOK_SECRET = b"whsec_a test's signing secret"
_CUSTOMER = "cus_test"
_MONTH_S = 30 * 24 * 60 * 60


class _NoMail:
    """A mailer for tests that send none."""

    def send(self, letter: Letter) -> None:
        raise AssertionError(f"no email in these tests, but one to {letter.to}")


@dataclass(slots=True)
class _Stripe:
    """Stripe as these tests play it: each customer's subscriptions, and every call it's sent.

    `troubles` answer the first calls, in order, before Stripe answers as it should.
    """

    subscriptions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    asked: list[httpx.Request] = field(default_factory=list)
    troubles: list[httpx.Response | Exception] = field(default_factory=list)

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Stripe's answer to `request`: a trouble while any is left, else the subscriptions."""
        self.asked.append(request)
        if self.troubles:
            trouble = self.troubles.pop(0)
            if isinstance(trouble, Exception):
                raise trouble
            return trouble
        customer = request.url.params["customer"]
        listed = self.subscriptions.get(customer, [])
        return httpx.Response(200, json={"object": "list", "data": listed, "has_more": False})


def _keeping_accounts(
    app: FastAPI, tmp_path, monkeypatch, *, billing: Billing | None
) -> UserStore:
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
        billing=billing,
    )
    monkeypatch.setattr(app.state, "accounts", accounts)
    monkeypatch.setattr(app.state, "meters", AccountMeters(accounts, allowance))
    return store


@pytest.fixture
def users(app: FastAPI, tmp_path, monkeypatch) -> UserStore:
    """Accounts on a server that takes no payment."""
    return _keeping_accounts(app, tmp_path, monkeypatch, billing=None)


@pytest.fixture
def stripe() -> _Stripe:
    """Stripe as these tests play it, with no subscriptions yet."""
    return _Stripe()


@pytest.fixture
def paying(app: FastAPI, tmp_path, monkeypatch, stripe: _Stripe) -> UserStore:
    """Accounts on a server that takes payment through `stripe`, retrying without a wait."""
    monkeypatch.setattr(constants, "STRIPE_FIRST_WAIT_S", 0)
    api = stripe_api("sk_test_key", transport=httpx.MockTransport(stripe.answer))
    billing = Billing(api, webhook_secret=_WEBHOOK_SECRET)
    return _keeping_accounts(app, tmp_path, monkeypatch, billing=billing)


def _webhook(
    reader: TestClient,
    event: str,
    *,
    kind: str = "customer.subscription.updated",
    customer: str | None = _CUSTOMER,
    signed_at: int | None = None,
    secret: bytes = _WEBHOOK_SECRET,
) -> httpx.Response:
    """Post an event to the webhook as Stripe does, signed with `secret` at `signed_at`."""
    at = int(time.time()) if signed_at is None else signed_at
    about = {} if customer is None else {"customer": customer}
    said = {"id": event, "type": kind, "created": at, "data": {"object": about}}
    payload = json.dumps(said).encode()
    signature = hmac.new(secret, f"{at}.".encode() + payload, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={at},v1={signature}"}
    return reader.post("/api/account/billing-events", content=payload, headers=headers)


def _subscription(status: str, *, until: int, account: str) -> dict[str, Any]:
    """A subscription as Stripe lists it, made by `account`'s checkout, paid to `until`."""
    return {
        "status": status,
        "metadata": {"account": account},
        "items": {"data": [{"current_period_end": until}]},
    }


def _signed_in_as(reader: TestClient, users: UserStore, email: str = _EMAIL) -> str:
    """Sign `reader` in to the account under `email`, made if need be; its id."""
    user = users.joined(email, now=0)
    cookie = signed(user.id, purpose="signed_in", until=2**40, secret=_SECRET)
    reader.cookies.set(SIGNED_IN, cookie)
    return user.id


def _paid(account: str, *, event: str, until: int | None, read_at: int) -> BillingEvent:
    """An event setting `account`'s paid time to `until`, as Stripe said asked at `read_at`."""
    return BillingEvent(
        event,
        type="customer.subscription.updated",
        created=0,
        customer="cus_test",
        account=account,
        outcome="applied",
        paid_until=until,
        read_at=read_at,
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
    users.apply_billing(_paid(account, event="evt_paid", until=now + 3600, read_at=1), now=now)
    assert_equal(upload(reader, files[1]).status_code, 201, "paid: past the month's one")
    # The month ended a minute ago, and its renewal is still being charged.
    renewing = _paid(account, event="evt_renewing", until=now - 60, read_at=2)
    users.apply_billing(renewing, now=now)
    assert_equal(upload(reader, files[2]).status_code, 201, "within the renewal's leeway")
    ran_out = now - constants.RENEWAL_LEEWAY_S - 60
    users.apply_billing(_paid(account, event="evt_unpaid", until=ran_out, read_at=3), now=now)
    assert_problem(upload(reader, files[3]), "monthly_documents_used", 403)


def test_an_event_sent_again_or_read_of_stripe_before_the_last_changes_nothing(
    users, browser: Callable[[], TestClient], pdf_bytes
):
    """Stripe sends an event again until it hears back, and two can land out of order.

    The paid time kept is from the latest read of Stripe, wherever it was made.
    """
    reader = browser()
    account = _signed_in_as(reader, users)
    files = _files(pdf_bytes, 3)
    upload(reader, files[0])
    now = int(time.time())
    paid = _paid(account, event="evt_paid", until=now + 3600, read_at=1)
    assert_equal(users.apply_billing(paid, now=now), "applied", "new")
    cancelled = _paid(account, event="evt_cancelled", until=None, read_at=3)
    assert_equal(users.apply_billing(cancelled, now=now), "applied", "new")
    assert_equal(users.apply_billing(paid, now=now), None, "the first, sent again")
    assert_problem(upload(reader, files[1]), "monthly_documents_used", 403)
    # Asked of Stripe before the cancellation was, its answer landing after.
    late = _paid(account, event="evt_late", until=now + 3600, read_at=2)
    assert_equal(users.apply_billing(late, now=now), "superseded", "kept, not applied")
    assert_problem(upload(reader, files[2]), "monthly_documents_used", 403)


def test_a_subscription_stripe_tells_of_lifts_the_limit_and_a_failed_renewal_brings_it_back(
    paying, stripe, browser: Callable[[], TestClient], pdf_bytes
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    files = _files(pdf_bytes, 3)
    upload(reader, files[0])
    assert_problem(upload(reader, files[1]), "monthly_documents_used", 403)
    now = int(time.time())
    stripe.subscriptions[_CUSTOMER] = [
        _subscription("active", until=now + _MONTH_S, account=account)
    ]
    paid = _webhook(reader, "evt_paid", kind="checkout.session.completed")
    assert_equal(paid.status_code, 204, "heard")
    assert_equal(upload(reader, files[1]).status_code, 201, "past the month's one")
    [asked] = stripe.asked
    assert_equal(asked.headers["Authorization"], "Bearer sk_test_key", "the key")
    assert_equal(asked.headers["Stripe-Version"], constants.STRIPE_API_VERSION, "pinned")
    # The renewal's charge failed: Stripe moved the period on, but the month isn't paid.
    stripe.subscriptions[_CUSTOMER] = [
        _subscription("past_due", until=now + 2 * _MONTH_S, account=account)
    ]
    _webhook(reader, "evt_failed", kind="invoice.payment_failed")
    assert_problem(upload(reader, files[2]), "monthly_documents_used", 403)
    ledger = [(each.id, each.outcome, each.paid_until) for each in paying.billing_of(account)]
    expected = [("evt_paid", "applied", now + _MONTH_S), ("evt_failed", "applied", None)]
    assert_equal(ledger, expected, "the ledger")


def test_an_event_sent_again_is_kept_once_and_stripe_is_asked_once(
    paying, stripe, browser: Callable[[], TestClient]
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    for _sent in range(2):
        assert_equal(_webhook(reader, "evt_once").status_code, 204, "heard each time")
    assert_equal(len(stripe.asked), 1, "Stripe asked")
    assert_equal(len(paying.billing_of(account)), 1, "kept in the ledger")


def test_an_event_not_signed_by_stripe_lately_is_not_found_and_changes_nothing(
    paying, stripe, browser: Callable[[], TestClient]
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    stale = int(time.time()) - constants.STRIPE_SIGNATURE_AGE_S - 60
    refused = [
        _webhook(reader, "evt_forged", secret=b"whsec_someone else's"),
        _webhook(reader, "evt_replayed", signed_at=stale),
        reader.post("/api/account/billing-events", content=b"{}"),
        # A time too long to be one, which Python's int refuses to read.
        reader.post(
            "/api/account/billing-events",
            content=b"{}",
            headers={"Stripe-Signature": f"t={'9' * 5000},v1=x"},
        ),
    ]
    # Signed for one body, sent with another.
    good = _webhook(reader, "evt_good")
    altered = good.request.content.replace(b"evt_good", b"evt_bad")
    refused.append(
        reader.post(
            "/api/account/billing-events",
            content=altered,
            headers={"Stripe-Signature": good.request.headers["Stripe-Signature"]},
        )
    )
    for each in refused:
        assert_problem(each, "not_found", 404)
    assert_equal([each.id for each in paying.billing_of(account)], ["evt_good"], "kept")


def test_stripe_failing_for_a_moment_is_asked_again_with_the_same_request(
    paying, stripe, browser: Callable[[], TestClient]
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("active", until=until, account=account)]
    stripe.troubles = [httpx.ConnectError("no route to Stripe"), httpx.Response(500)]
    assert_equal(_webhook(reader, "evt_paid").status_code, 204, "heard, at the third try")
    assert_equal(len({str(each.url) for each in stripe.asked}), 1, "one request, sent again")
    assert_equal(len(stripe.asked), 3, "tries")
    assert_equal(paying.billing_of(account)[0].paid_until, until, "set")


def test_stripe_down_past_every_try_leaves_the_event_for_stripe_to_send_again(
    paying, stripe, browser: Callable[[], TestClient]
):
    """Answered 503, Stripe sends the event again later: nothing is kept until it's applied."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    stripe.troubles = [httpx.Response(503)] * constants.STRIPE_ATTEMPTS
    assert_problem(_webhook(reader, "evt_paid"), "payments_unavailable", 503)
    assert_equal(paying.billing_of(account), [], "nothing kept")
    # Refused for good, as a bad key is: tried once.
    stripe.troubles = [httpx.Response(401, json={"error": {"type": "invalid_request_error"}})]
    assert_problem(_webhook(reader, "evt_paid"), "payments_unavailable", 503)
    assert_equal(len(stripe.asked), constants.STRIPE_ATTEMPTS + 1, "tries")
    assert_equal(_webhook(reader, "evt_paid").status_code, 204, "sent again, once it answers")
    assert_equal(len(paying.billing_of(account)), 1, "kept")


def test_an_event_billing_doesnt_act_on_is_kept_and_stripe_isnt_asked(
    paying, stripe, browser: Callable[[], TestClient], caplog
):
    """A payment for no account, or a dispute, is whoever runs the server's to look into."""
    reader = browser()
    with caplog.at_level(logging.WARNING, logger="squidpdf.accounts"):
        _webhook(reader, "evt_price", kind="price.updated", customer=None)
        _webhook(reader, "evt_stranger", kind="invoice.paid", customer="cus_from_the_dashboard")
        _webhook(reader, "evt_disputed", kind="charge.dispute.created", customer=None)
    assert_equal(stripe.asked, [], "Stripe asked")
    for event in ("evt_price", "evt_stranger", "evt_disputed"):
        assert_equal(paying.has_billing_event(event), True, f"{event} kept")
    assert_in("billing_customer_unknown", caplog.text, "a payment for no account, said")
    assert_in("billing_disputed", caplog.text, "a dispute, said")


def test_only_the_accounts_own_subscription_still_collected_lifts_the_limit(
    paying, stripe, browser: Callable[[], TestClient], caplog
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    paused = _subscription("active", until=until, account=account) | {
        "pause_collection": {"behavior": "void"}
    }
    # One Stripe bills the same customer for something else, and one whose collection is paused.
    stripe.subscriptions[_CUSTOMER] = [
        _subscription("active", until=until, account="another account"),
        paused,
    ]
    with caplog.at_level(logging.WARNING, logger="squidpdf.accounts"):
        assert_equal(_webhook(reader, "evt_paid").status_code, 204, "heard")
    assert_equal(paying.billing_of(account)[0].paid_until, None, "no paid time")
    assert_in("billing_subscribed_twice", caplog.text, "two billing at once, said")


def test_a_customer_stripe_hasnt_got_pays_for_nothing_and_is_asked_once(
    paying, stripe, browser: Callable[[], TestClient]
):
    """As one made in test mode, the server since on its live key, or deleted in Stripe."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    missing = {"error": {"type": "invalid_request_error", "code": "resource_missing"}}
    stripe.troubles = [httpx.Response(400, json=missing)]
    assert_equal(_webhook(reader, "evt_paid").status_code, 204, "heard")
    assert_equal(len(stripe.asked), 1, "asked once: it won't be there next time either")
    assert_equal(paying.billing_of(account)[0].paid_until, None, "no paid time")


def test_an_account_stripe_bills_still_is_kept_and_one_it_bills_nothing_removed(
    paying, stripe, app: FastAPI
):
    """`squidpdf remove` asks this; the command line can't be handed a Stripe of a test's."""
    billing = app.state.accounts.billing
    account = paying.invite(_EMAIL, now=0, is_unlimited=False).id
    paying.keep_customer(account, _CUSTOMER)
    # A renewal's charge failed, and Stripe tries the card again: it may yet bill them.
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("past_due", until=until, account=account)]
    assert_equal(removal_of(paying, billing, _EMAIL), "subscribed", "billed still")
    assert_equal(removal_of(paying, None, _EMAIL), "unchecked", "Stripe not set up here")
    stripe.subscriptions[_CUSTOMER] = [_subscription("canceled", until=until, account=account)]
    assert_equal(removal_of(paying, billing, _EMAIL), "removed", "billed nothing")


def test_a_resync_asks_stripe_again_and_keeps_its_answer_in_the_ledger(
    paying, stripe, app: FastAPI, browser: Callable[[], TestClient]
):
    """For an event lost, or kept wrong: Stripe's own resent has an id the ledger has."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    _webhook(reader, "evt_paid")
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("active", until=until, account=account)]
    user = paying.joined(_EMAIL, now=0)  # the account signed in, as it reads now
    kept = resync(paying, app.state.accounts.billing, user, customer=_CUSTOMER)
    assert_equal(kept, "applied", "what the resync did")
    ledger = paying.billing_of(account)
    kinds = [each.type for each in ledger]
    assert_equal(kinds, ["customer.subscription.updated", "squidpdf.resync"], "kept, in order")
    assert_equal(ledger[-1].paid_until, until, "the paid time Stripe said")


@pytest.mark.parametrize(
    ("named", "refused_for"),
    [
        ({"SQUIDPDF_STRIPE_KEY": "sk_test_key"}, "both"),
        (
            {"SQUIDPDF_STRIPE_KEY": "pk_test_key", "SQUIDPDF_STRIPE_WEBHOOK_SECRET": "whsec_x"},
            "no Stripe secret key",
        ),
        (
            {"SQUIDPDF_STRIPE_KEY": "sk_test_key", "SQUIDPDF_STRIPE_WEBHOOK_SECRET": "x"},
            "no signing secret",
        ),
    ],
    ids=["one of two", "a publishable key", "no signing secret"],
)
def test_payments_set_half_or_with_the_wrong_keys_stop_the_start(
    tmp_path, monkeypatch, named, refused_for
):
    """Said as the server starts, not on the first payment; no secret is in what's said."""
    accounts_need = {
        "SQUIDPDF_DATABASE": str(tmp_path / "users.sqlite3"),
        "SQUIDPDF_SECRET": _SECRET.decode(),
        "SQUIDPDF_SITE": "https://pdf.example.com",
        "SQUIDPDF_MAIL_FROM": "sign-in@example.com",
        "SQUIDPDF_BREVO_KEY": "a-brevo-key",
        "SQUIDPDF_FREE_DOCUMENTS": "1",
        "SQUIDPDF_DOCUMENTS_A_MONTH": "1",
    }
    for name, value in (accounts_need | named).items():
        monkeypatch.setenv(name, value)
    with pytest.raises(AccountsUnset, match=refused_for) as refused:
        create_app()
    assert_equal("sk_test_key" in str(refused.value), False, "the key in what's said")


def test_payments_with_nothing_to_lift_or_no_accounts_stop_the_start(tmp_path, monkeypatch):
    stripe = {"SQUIDPDF_STRIPE_KEY": "sk_test_key", "SQUIDPDF_STRIPE_WEBHOOK_SECRET": "whsec_x"}
    for name, value in stripe.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SQUIDPDF_DATABASE", raising=False)
    with pytest.raises(AccountsUnset, match="an account is what pays"):
        create_app()
    for name, value in {
        "SQUIDPDF_DATABASE": str(tmp_path / "users.sqlite3"),
        "SQUIDPDF_SECRET": _SECRET.decode(),
        "SQUIDPDF_SITE": "https://pdf.example.com",
        "SQUIDPDF_MAIL_FROM": "sign-in@example.com",
        "SQUIDPDF_BREVO_KEY": "a-brevo-key",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(AccountsUnset, match="payment lifts a limit"):
        create_app()
