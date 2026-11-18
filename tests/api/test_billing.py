"""Paying for no document limit: what Stripe says is paid, kept in a ledger, lifts the limit."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.accounts import constants
from squidpdf.accounts.billing.removal import remove_account
from squidpdf.accounts.billing.stripe_api import stripe_api
from squidpdf.accounts.billing.webhook import resync
from squidpdf.accounts.cookies import SIGNED_IN
from squidpdf.accounts.deployment import Accounts, AccountsUnset, Allowance, Billing
from squidpdf.accounts.mail import Letter, MailController
from squidpdf.accounts.metering import AccountMeters
from squidpdf.accounts.signed import signed
from squidpdf.accounts.types import BillingEvent
from squidpdf.accounts.users import UserStore, open_users
from squidpdf.api.app import create_app
from tests.api.conftest import upload
from tests.helpers import assert_close, assert_equal, assert_in, assert_problem

_SECRET = b"a test's secret, long enough to sign with"
_EMAIL = "payer@example.com"
_WEBHOOK_SECRET = b"whsec_a test's signing secret"
_CUSTOMER = "cus_test"
_PRICE = "price_monthly"
_MONTH_S = 30 * 24 * 60 * 60
_SITE = "https://pdf.example.com"
# What Stripe answers each POST with, as far as billing reads it.
_MADE = {
    "/v1/customers": {"id": "cus_made"},
    "/v1/checkout/sessions": {"url": "https://checkout.stripe.com/c/pay/cs_test"},
    "/v1/billing_portal/sessions": {"url": "https://billing.stripe.com/p/session/test"},
}
# Payment set up whole in the environment, as env.example says.
_STRIPE = {
    "SQUIDPDF_STRIPE_KEY": "sk_test_key",
    "SQUIDPDF_STRIPE_WEBHOOK_SECRET": "whsec_x",
    "SQUIDPDF_STRIPE_PRICE": "price_monthly",
}


class _NoMail:
    """A mailer for tests that send none."""

    def send(self, letter: Letter) -> None:
        raise AssertionError(f"no email in these tests, but one to {letter.to}")


@dataclass(slots=True)
class _Stripe:
    """Stripe as these tests play it: each customer's subscriptions, and every call it's sent.

    `troubles` answer the first calls, in order, before Stripe answers as it should; a
    customer `gone` is one Stripe hasn't got; `charges` names each charge's customer; a
    cancel of a subscription `lost` goes through, but its answer is lost on the way.
    """

    subscriptions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    asked: list[httpx.Request] = field(default_factory=list)
    troubles: list[httpx.Response | Exception] = field(default_factory=list)
    gone: set[str] = field(default_factory=set)
    charges: dict[str, str] = field(default_factory=dict)
    lost: set[str] = field(default_factory=set)

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Stripe's answer to `request`: a trouble while any is left, else as Stripe would."""
        self.asked.append(request)
        if self.troubles:
            trouble = self.troubles.pop(0)
            if isinstance(trouble, Exception):
                raise trouble
            return trouble
        named = request.url.path.rsplit("/", 1)[-1]
        if request.method == "DELETE":
            return self._cancelled(named)
        if request.url.path.startswith("/v1/charges/"):
            return httpx.Response(200, json={"id": named, "customer": self.charges[named]})
        if request.method == "POST":
            customer = parse_qs(request.content.decode()).get("customer", [""])[0]
            if customer in self.gone:
                missing = {"type": "invalid_request_error", "code": "resource_missing"}
                return httpx.Response(400, json={"error": missing})
            return httpx.Response(200, json=_MADE[request.url.path])
        customer = request.url.params["customer"]
        listed = self.subscriptions.get(customer, [])
        return httpx.Response(200, json={"object": "list", "data": listed, "has_more": False})

    def _cancelled(self, subscription: str) -> httpx.Response:
        """The subscription by the id `subscription`, cancelled now; none, if it's ended."""
        for each in itertools.chain.from_iterable(self.subscriptions.values()):
            if each["id"] == subscription and each["status"] != "canceled":
                each["status"] = "canceled"
                if subscription in self.lost:
                    self.lost.discard(subscription)
                    raise httpx.ReadTimeout("Stripe's answer never arrived")
                return httpx.Response(200, json=each)
        missing = {"type": "invalid_request_error", "code": "resource_missing"}
        return httpx.Response(404, json={"error": missing})

    def cancelled(self) -> list[tuple[str, dict[str, list[str]]]]:
        """Each subscription Stripe was told to cancel, and the form that said why."""
        return [
            (each.url.path.rsplit("/", 1)[-1], parse_qs(each.content.decode()))
            for each in self.asked
            if each.method == "DELETE"
        ]

    def posted(self) -> list[tuple[str, dict[str, list[str]], str]]:
        """Each POST Stripe was sent: its path, its form, and its idempotency key."""
        return [
            (each.url.path, parse_qs(each.content.decode()), each.headers["Idempotency-Key"])
            for each in self.asked
            if each.method == "POST"
        ]


def _keeping_accounts(
    app: FastAPI, tmp_path, monkeypatch, *, billing: Billing | None
) -> UserStore:
    """The app keeping accounts in a fresh table, an account opening one document a month."""
    allowance = Allowance(free=1, monthly=1)
    store = open_users(tmp_path / "users.sqlite3")
    accounts = Accounts(
        store,
        MailController(_NoMail(), store, secret=_SECRET),
        secret=_SECRET,
        site=_SITE,
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
    billing = Billing(api, webhook_secret=_WEBHOOK_SECRET, price=_PRICE)
    return _keeping_accounts(app, tmp_path, monkeypatch, billing=billing)


def _webhook(
    reader: TestClient,
    event: str,
    *,
    kind: str = "customer.subscription.updated",
    customer: str | None = _CUSTOMER,
    charge: str | None = None,
    signed_at: int | None = None,
    secret: bytes = _WEBHOOK_SECRET,
) -> httpx.Response:
    """Post an event to the webhook as Stripe does, signed with `secret` at `signed_at`."""
    at = int(time.time()) if signed_at is None else signed_at
    named = {"customer": customer, "charge": charge}
    about = {key: value for key, value in named.items() if value is not None}
    said = {"id": event, "type": kind, "created": at, "data": {"object": about}}
    payload = json.dumps(said).encode()
    signature = hmac.new(secret, f"{at}.".encode() + payload, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={at},v1={signature}"}
    return reader.post("/api/account/billing-events", content=payload, headers=headers)


def _dispute_event(
    reader: TestClient, event: str, *, kind: str = "charge.dispute.created"
) -> httpx.Response:
    """Post an event about a dispute of the charge `ch_disputed`, which names no customer."""
    return _webhook(reader, event, kind=kind, customer=None, charge="ch_disputed")


def _subscription(
    status: str, *, until: int, account: str, subscription: str = "sub_test", created: int = 0
) -> dict[str, Any]:
    """A subscription as Stripe lists it, made by `account`'s checkout, paid to `until`."""
    return {
        "id": subscription,
        "created": created,
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


def _paid(account: str, *, event: str, until: int | None, paid_read_at: int) -> BillingEvent:
    """An event setting `account`'s paid time to `until`, as Stripe said at `paid_read_at`."""
    return BillingEvent(
        event,
        type="customer.subscription.updated",
        created=0,
        customer="cus_test",
        account=account,
        outcome="applied",
        paid_until=until,
        paid_read_at=paid_read_at,
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
    users.apply_billing(
        _paid(account, event="evt_paid", until=now + 3600, paid_read_at=1), now=now
    )
    assert_equal(upload(reader, files[1]).status_code, 201, "paid: past the month's one")
    signed_in = reader.get("/api/account").json()["signed_in"]
    assert_equal(signed_in["documents_left"], None, "no count while no limit applies")
    # The month ended a minute ago, and its renewal is still being charged.
    renewing = _paid(account, event="evt_renewing", until=now - 60, paid_read_at=2)
    users.apply_billing(renewing, now=now)
    assert_equal(upload(reader, files[2]).status_code, 201, "within the renewal's leeway")
    ran_out = now - constants.RENEWAL_LEEWAY_S - 60
    users.apply_billing(
        _paid(account, event="evt_unpaid", until=ran_out, paid_read_at=3), now=now
    )
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
    paid = _paid(account, event="evt_paid", until=now + 3600, paid_read_at=1)
    assert_equal(users.apply_billing(paid, now=now), "applied", "new")
    cancelled = _paid(account, event="evt_cancelled", until=None, paid_read_at=3)
    assert_equal(users.apply_billing(cancelled, now=now), "applied", "new")
    assert_equal(users.apply_billing(paid, now=now), None, "the first, sent again")
    assert_problem(upload(reader, files[1]), "monthly_documents_used", 403)
    # Asked of Stripe before the cancellation was, its answer landing after.
    late = _paid(account, event="evt_late", until=now + 3600, paid_read_at=2)
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
    ledger = [(each.id, each.outcome, each.paid_until) for each in paying.ledger_of(account)]
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
    assert_equal(len(paying.ledger_of(account)), 1, "kept in the ledger")


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
    assert_equal([each.id for each in paying.ledger_of(account)], ["evt_good"], "kept")


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
    assert_equal(paying.ledger_of(account)[0].paid_until, until, "set")


def test_stripe_down_past_every_try_leaves_the_event_for_stripe_to_send_again(
    paying, stripe, browser: Callable[[], TestClient]
):
    """Answered 503, Stripe sends the event again later: nothing is kept until it's applied."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    stripe.troubles = [httpx.Response(503)] * constants.STRIPE_ATTEMPTS
    assert_problem(_webhook(reader, "evt_paid"), "payments_unavailable", 503)
    assert_equal(paying.ledger_of(account), [], "nothing kept")
    # Refused for good, as a bad key is: tried once.
    stripe.troubles = [httpx.Response(401, json={"error": {"type": "invalid_request_error"}})]
    assert_problem(_webhook(reader, "evt_paid"), "payments_unavailable", 503)
    assert_equal(len(stripe.asked), constants.STRIPE_ATTEMPTS + 1, "tries")
    assert_equal(_webhook(reader, "evt_paid").status_code, 204, "sent again, once it answers")
    assert_equal(len(paying.ledger_of(account)), 1, "kept")


def test_an_event_billing_doesnt_act_on_is_kept_and_stripe_isnt_asked(
    paying, stripe, browser: Callable[[], TestClient], caplog
):
    """A payment for no account, or how a dispute ends, is for whoever runs the server."""
    reader = browser()
    with caplog.at_level(logging.WARNING, logger="squidpdf.accounts"):
        _webhook(reader, "evt_price", kind="price.updated", customer=None)
        _webhook(reader, "evt_stranger", kind="invoice.paid", customer="cus_from_the_dashboard")
        _dispute_event(reader, "evt_disputed", kind="charge.dispute.closed")
    assert_equal(stripe.asked, [], "Stripe asked")
    for event in ("evt_price", "evt_stranger", "evt_disputed"):
        assert_equal(paying.has_ledger_event(event), True, f"{event} kept")
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
    assert_equal(paying.ledger_of(account)[0].paid_until, None, "no paid time")
    assert_in("billing_subscribed_twice", caplog.text, "two billing at once, said")


def test_a_dispute_ends_the_accounts_own_subscription_at_once_and_brings_its_limit_back(
    paying, stripe, browser: Callable[[], TestClient], pdf_bytes
):
    """Whoever disputes a charge won't pay the next, and each dispute costs a fee: none follows.

    Only what the account's checkout made: one billed for anything else is the server's to end.
    """
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    own = _subscription("active", until=until, account=account, subscription="sub_own")
    other = _subscription("active", until=until, account="another", subscription="sub_other")
    stripe.subscriptions[_CUSTOMER] = [own, other]
    stripe.charges["ch_disputed"] = _CUSTOMER
    files = _files(pdf_bytes, 3)
    _webhook(reader, "evt_paid")
    upload(reader, files[0])
    assert_equal(upload(reader, files[1]).status_code, 201, "paid: past the month's one")
    assert_equal(_dispute_event(reader, "evt_disputed").status_code, 204, "heard")
    [(cancelled, form)] = stripe.cancelled()
    assert_equal(cancelled, "sub_own", "the account's own, cancelled")
    assert_in("evt_disputed", form["cancellation_details[comment]"][0], "why, kept in Stripe")
    assert_equal(other["status"], "active", "another's, left to whoever runs the server")
    assert_problem(upload(reader, files[2]), "monthly_documents_used", 403)
    kept = paying.ledger_of(account)[-1]
    assert_equal((kept.customer, kept.paid_until), (_CUSTOMER, None), "kept, with its customer")
    assert_equal(_dispute_event(reader, "evt_disputed").status_code, 204, "sent again")
    assert_equal(len(stripe.cancelled()), 1, "kept already: nothing cancelled again")


def test_a_dispute_leaves_a_subscription_bought_since_billing(
    paying, stripe, browser: Callable[[], TestClient]
):
    """Stripe sends a dispute again until it hears back: maybe after the account paid afresh."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    disputed = _subscription("active", until=until, account=account, subscription="sub_old")
    since = _subscription(
        "active", until=until, account=account, subscription="sub_new", created=2**40
    )
    stripe.subscriptions[_CUSTOMER] = [disputed, since]
    stripe.charges["ch_disputed"] = _CUSTOMER
    assert_equal(_dispute_event(reader, "evt_disputed").status_code, 204, "heard")
    assert_equal([each for each, _form in stripe.cancelled()], ["sub_old"], "the one disputed")
    assert_equal(paying.ledger_of(account)[-1].paid_until, until, "paid, by the one since")


def test_a_cancel_whose_answer_was_lost_is_found_done_on_the_retry(
    paying, stripe, browser: Callable[[], TestClient], caplog
):
    """Stripe ended it, but never said so: the retry finds it gone, and the event is kept."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("active", until=until, account=account)]
    stripe.charges["ch_disputed"] = _CUSTOMER
    stripe.lost = {"sub_test"}
    with caplog.at_level(logging.WARNING, logger="squidpdf.accounts"):
        assert_equal(_dispute_event(reader, "evt_disputed").status_code, 204, "heard")
    assert_equal(len(stripe.cancelled()), 2, "sent again, once its answer was lost")
    assert_in(
        "stripe_cancelled call=/v1/subscriptions/sub_test already=true",
        caplog.text,
        "found done, said",
    )
    assert_equal(paying.ledger_of(account)[-1].paid_until, None, "kept, with no paid time")


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
    assert_equal(paying.ledger_of(account)[0].paid_until, None, "no paid time")


def test_an_account_stripe_bills_still_is_kept_and_one_it_bills_nothing_removed(
    paying, stripe, app: FastAPI
):
    """`squidpdf remove` asks this; the command line can't be handed a Stripe of a test's."""
    billing = app.state.accounts.billing
    account = paying.invite(_EMAIL, now=0, is_unlimited=False, secret=_SECRET).id
    paying.keep_customer(account, _CUSTOMER)
    # A renewal's charge failed, and Stripe tries the card again: it may yet bill them.
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("past_due", until=until, account=account)]
    stripe_here = lambda: billing  # noqa: E731 (the server's Stripe, as the CLI would find it)
    assert_equal(remove_account(paying, _EMAIL, billing_here=stripe_here), "paying", "billed")
    stripe.subscriptions[_CUSTOMER] = [_subscription("canceled", until=until, account=account)]
    assert_equal(remove_account(paying, _EMAIL, billing_here=stripe_here), "removed", "not")


def test_a_resync_asks_stripe_again_and_keeps_its_answer_in_the_ledger(
    paying, stripe, app: FastAPI, browser: Callable[[], TestClient]
):
    """`squidpdf billing --resync` asks this; the command line can't be handed a test's Stripe.

    For an event lost, or kept wrong: Stripe's own resent has an id the ledger has.
    """
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    _webhook(reader, "evt_paid")
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("active", until=until, account=account)]
    user = paying.joined(_EMAIL, now=0)  # the account signed in, as it reads now
    kept = resync(paying, app.state.accounts.billing, user, customer=_CUSTOMER)
    assert_equal(kept, "applied", "what the resync did")
    ledger = paying.ledger_of(account)
    kinds = [each.type for each in ledger]
    assert_equal(kinds, ["customer.subscription.updated", "squidpdf.resync"], "kept, in order")
    assert_equal(ledger[-1].paid_until, until, "the paid time Stripe said")


@pytest.mark.parametrize(
    ("named", "refused_for"),
    [
        ({"SQUIDPDF_STRIPE_KEY": "sk_test_key"}, "all of"),
        (_STRIPE | {"SQUIDPDF_STRIPE_KEY": "pk_test_key"}, "no Stripe secret key"),
        (_STRIPE | {"SQUIDPDF_STRIPE_WEBHOOK_SECRET": "x"}, "no signing secret"),
        (_STRIPE | {"SQUIDPDF_STRIPE_PRICE": "prod_monthly"}, "no Stripe price"),
    ],
    ids=["one of three", "a publishable key", "no signing secret", "a product, not a price"],
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
    for name, value in _STRIPE.items():
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


def test_an_account_goes_to_stripes_checkout_its_customer_made_once(
    paying, stripe, browser: Callable[[], TestClient]
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    went = reader.post("/api/account/checkout")
    assert_equal(went.json(), {"url": _MADE["/v1/checkout/sessions"]["url"]}, "Stripe's page")
    (made, made_form, made_key), (paid, paid_form, first_key) = stripe.posted()
    assert_equal(made, "/v1/customers", "a customer first")
    assert_equal(made_form["email"], [_EMAIL], "their address, for receipts")
    assert_equal(made_key.startswith("squidpdf-customer-"), True, "a key of the call's own")
    assert_equal(made_form["metadata[account]"], [account], "the account it's for")
    assert_equal(paid, "/v1/checkout/sessions", "then the checkout")
    for name, value in {
        "customer": "cus_made",
        "mode": "subscription",
        "line_items[0][price]": _PRICE,
        "automatic_tax[enabled]": "true",
        "success_url": f"{_SITE}/?billing=paid",
        "subscription_data[metadata][account]": account,
    }.items():
        assert_equal(paid_form[name], [value], name)
    expires_in = int(paid_form["expires_at"][0]) - time.time()
    assert_close(
        expires_in, constants.CHECKOUT_VALID_S, within=60, label="an old tab can't pay"
    )
    reader.post("/api/account/checkout")
    *_, (again, _again_form, second_key) = stripe.posted()
    assert_equal(len(stripe.posted()), 3, "no second customer")
    assert_equal(second_key == first_key, False, "a checkout asked for again is a new one")
    state = reader.get("/api/account").json()
    assert_equal(state["takes_payment"], True, "the browser offers payment")
    assert_equal(state["signed_in"]["has_billing"], True, "and billing to manage")


def test_a_call_with_no_answer_is_sent_again_with_its_key_so_stripe_does_it_once(
    paying, stripe, browser: Callable[[], TestClient]
):
    """Timed out, the customer may have been made: the same key gets Stripe's same answer."""
    reader = browser()
    _signed_in_as(reader, paying)
    stripe.troubles = [httpx.ReadTimeout("Stripe was slow to answer")]
    assert_equal(reader.post("/api/account/checkout").status_code, 200, "paid, on the retry")
    tried, retried, _checkout = stripe.posted()
    assert_equal(tried, retried, "the same call, key and all")


def test_paying_again_or_for_an_unlimited_friend_is_refused_and_stripe_isnt_asked(
    paying, stripe, browser: Callable[[], TestClient]
):
    friend = browser()
    paying.invite("friend@example.com", now=0, is_unlimited=True, secret=_SECRET)
    _signed_in_as(friend, paying, "friend@example.com")
    assert_problem(friend.post("/api/account/checkout"), "already_unlimited", 409)
    payer = browser()
    account = _signed_in_as(payer, paying)
    now = int(time.time())
    paying.apply_billing(
        _paid(account, event="evt_paid", until=now + _MONTH_S, paid_read_at=1), now=now
    )
    assert_problem(payer.post("/api/account/checkout"), "already_unlimited", 409)
    assert_equal(
        payer.get("/api/account").json()["signed_in"]["paid_until"], now + _MONTH_S, ""
    )
    assert_equal(stripe.asked, [], "Stripe asked")


def test_going_to_pay_signed_out_is_refused(paying, browser: Callable[[], TestClient]):
    for path in ("/api/account/checkout", "/api/account/billing-portal"):
        assert_problem(browser().post(path), "sign_in_to_pay", 401)


def test_a_server_that_takes_no_payment_says_so(users, browser: Callable[[], TestClient]):
    reader = browser()
    _signed_in_as(reader, users)
    for path in ("/api/account/checkout", "/api/account/billing-portal"):
        assert_problem(reader.post(path), "no_payments", 404)
    assert_problem(_webhook(reader, "evt_any"), "not_found", 404)
    assert_equal(reader.get("/api/account").json()["takes_payment"], False, "offered")


def test_the_portal_opens_for_an_account_that_went_to_pay_and_not_one_that_never_did(
    paying, stripe, browser: Callable[[], TestClient]
):
    reader = browser()
    account = _signed_in_as(reader, paying)
    assert_problem(reader.post("/api/account/billing-portal"), "no_billing", 404)
    paying.keep_customer(account, _CUSTOMER)
    opened = reader.post("/api/account/billing-portal")
    assert_equal(opened.json(), {"url": _MADE["/v1/billing_portal/sessions"]["url"]}, "portal")
    [(path, form, key)] = stripe.posted()
    assert_equal((path, form["customer"]), ("/v1/billing_portal/sessions", [_CUSTOMER]), "for")
    assert_equal(form["return_url"], [f"{_SITE}/"], "back to the app")
    assert_equal(key.startswith("squidpdf-portal-"), True, "its own key")


def test_an_account_stripe_bills_already_is_sent_to_manage_it_not_to_pay_again(
    paying, stripe, browser: Callable[[], TestClient]
):
    """Its paid time here can be late, or its renewal retried: Stripe is asked first."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, _CUSTOMER)
    until = int(time.time()) + _MONTH_S
    stripe.subscriptions[_CUSTOMER] = [_subscription("past_due", until=until, account=account)]
    assert_problem(reader.post("/api/account/checkout"), "already_subscribed", 409)
    assert_equal(stripe.posted(), [], "no checkout made")


def test_a_customer_stripe_hasnt_got_is_made_again_to_pay_and_has_no_billing_to_manage(
    paying, stripe, browser: Callable[[], TestClient]
):
    """As one made with the test key, the server since on its live key, or deleted in Stripe."""
    reader = browser()
    account = _signed_in_as(reader, paying)
    paying.keep_customer(account, "cus_gone")
    stripe.gone.add("cus_gone")
    assert_problem(reader.post("/api/account/billing-portal"), "no_billing", 404)
    paying.keep_customer(account, "cus_gone")
    went = reader.post("/api/account/checkout")
    assert_equal(went.status_code, 200, "Stripe's page, for a customer made again")
    paths = [path for path, _form, _key in stripe.posted()]
    expected = ["/v1/billing_portal/sessions", "/v1/checkout/sessions", "/v1/customers"]
    assert_equal(paths, [*expected, "/v1/checkout/sessions"], "what was made")
    assert_equal(paying.joined(_EMAIL, now=0).stripe_customer, "cus_made", "the customer kept")
