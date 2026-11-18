"""Stripe's API over HTTPS: each call tried again with care, and what Stripe says is paid.

A call is tried again only where Stripe says it may be, or gave no answer, and each try
sends the very same request: a POST's idempotency key with it, so Stripe does it once.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import Any, cast

import httpx

from squidpdf.accounts import constants
from squidpdf.accounts.errors import PaymentsUnavailable
from squidpdf.accounts.types import Subscriptions
from squidpdf.core import ErrorController, Failure, LogController, LogEvent, OwnText, Problem

_log = LogController.for_module(__name__)

_API_URL = "https://api.stripe.com"  # Stripe's API, every path below it
# Answers that are worth another try when Stripe doesn't say: a clash with a call under way
# on the same object or key, or too many calls at once. A 5xx is too.
_TRY_AGAIN = frozenset({HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS})
# A subscription in these has its month paid; past_due, unpaid and the rest have stopped.
_PAID_STATUSES = frozenset({"active", "trialing"})
# A subscription in these will never bill again; in any other, it bills, or may yet.
_ENDED_STATUSES = frozenset({"canceled", "incomplete_expired"})
# Stripe's code for an object it hasn't got: a customer deleted, or made with another key.
_MISSING = "resource_missing"
_NO_CODE = "no code"  # what the log says where an answer names no error
# Where Stripe's checkout sends a browser once it has paid: the page waits for the webhook.
_PAID_PATH = "/?billing=paid"


class NotAtStripe(Exception):
    """Stripe hasn't got what a call names: a customer deleted, or made in test mode."""


def _problem_of_refusal(failure: Exception) -> Problem:
    """Stripe's last refusal, its status and its error's code kept for the log."""
    refused = cast(httpx.HTTPStatusError, failure)  # _STRIPE_FAILURES hands it only these
    status = refused.response.status_code
    # Its code alone: the message beside it may quote what was sent, an address included.
    return PaymentsUnavailable(debug=f"Stripe answered {status} ({_code_in(refused.response)})")


def _code_in(answer: httpx.Response) -> str:
    """Stripe's words for why it refused, its error's type and code; _NO_CODE for none."""
    error = _error_in(answer)
    # .get: Stripe names a code for some errors, not all.
    return f"{error.get('type', _NO_CODE)}/{error.get('code', _NO_CODE)}"


def _error_in(answer: httpx.Response) -> dict[str, Any]:
    """The error Stripe's answer names, by its parts; empty where it names none."""
    try:
        said = answer.json()
    except ValueError:  # raised by the JSON reader: a proxy's page, not Stripe's answer
        return {}
    error = said.get("error") if isinstance(said, dict) else None  # .get: a proxy's may not
    return error if isinstance(error, dict) else {}


# Stripe's failures, after every try. Its refusal first: an HTTPStatusError is an HTTPError too.
_STRIPE_FAILURES = ErrorController(
    (
        Failure(httpx.HTTPStatusError, _problem_of_refusal),
        Failure(httpx.HTTPError, PaymentsUnavailable),
    )
)


@dataclass(frozen=True, slots=True, eq=False)
class StripeApi:
    """Stripe's API, its key and pinned version in each call; made by `stripe_api`."""

    client: httpx.Client  # carries the key: never logged

    def new_customer(self, *, account: str, email: str) -> str:
        """A new Stripe customer for `account`, by its id, sending receipts to `email`.

        Its key is this call's, not the account's: Stripe keeps a key's answer a day, so a
        server error kept under the account's would refuse it until then.
        """
        form = {"email": email, "metadata[account]": account}
        return self._post("/v1/customers", form=form, key=_one_call("customer"))["id"]

    def checkout_url(self, *, customer: str, account: str, price: str, site: str) -> str:
        """Where `customer` pays monthly for `price`, its tax worked out by Stripe Tax.

        Stripe's own page takes the card, so none reaches this server; back at `site` after.
        It takes payment CHECKOUT_VALID_S. Raises NotAtStripe for a customer Stripe hasn't got.
        """
        form = {
            "mode": "subscription",
            "customer": customer,
            "client_reference_id": account,
            "line_items[0][price]": price,
            "line_items[0][quantity]": "1",
            "automatic_tax[enabled]": "true",
            # Tax goes by where the customer is, so the address typed is kept on the customer.
            "billing_address_collection": "required",
            "customer_update[address]": "auto",
            "subscription_data[metadata][account]": account,
            "success_url": f"{site}{_PAID_PATH}",
            "cancel_url": f"{site}/",
            "expires_at": str(int(time.time()) + constants.CHECKOUT_VALID_S),
        }
        made = self._post("/v1/checkout/sessions", form=form, key=_one_call("checkout"))
        return made["url"]

    def portal_url(self, *, customer: str, site: str) -> str:
        """Where `customer` mends a card, reads invoices, cancels; NotAtStripe if it's gone."""
        form = {"customer": customer, "return_url": f"{site}/"}
        made = self._post("/v1/billing_portal/sessions", form=form, key=_one_call("portal"))
        return made["url"]

    def subscriptions(self, customer: str, *, account: str) -> Subscriptions:
        """What Stripe has of `customer`'s subscriptions now, paid time from `account`'s own.

        An account's own are those its checkout made, so a subscription Stripe bills for
        anything else lifts no limit. Raises PaymentsUnavailable when Stripe can't be asked.
        """
        # A hundred, Stripe's most a page: a customer never has that many subscriptions.
        params = {"customer": customer, "status": "all", "limit": "100"}
        try:
            listed = self._call(
                self.client.build_request("GET", "/v1/subscriptions", params=params)
            )
        except NotAtStripe:  # raised by _answer_to: a customer Stripe hasn't got pays nothing
            return Subscriptions(paid_until=None, live=0)
        listing = listed["data"]
        live = sum(each["status"] not in _ENDED_STATUSES for each in listing)
        return Subscriptions(paid_until=_paid_until_in(listing, account=account), live=live)

    def _post(self, path: str, *, form: dict[str, str], key: str) -> dict[str, Any]:
        """What Stripe made of `form` at `path`, made once for `key` however often it's sent."""
        headers = {"Idempotency-Key": key}
        made = self._call(self.client.build_request("POST", path, data=form, headers=headers))
        # The key ties this line to Stripe's own log of the request.
        _log.write(LogEvent.STRIPE_POSTED, call=OwnText(path), key=OwnText(key))
        return made

    def _call(self, request: httpx.Request) -> dict[str, Any]:
        """Stripe's answer to `request`, after up to STRIPE_ATTEMPTS tries; else it raises."""
        answer = _STRIPE_FAILURES.result_of(partial(self._answer_to, request))
        return answer.json()

    def _answer_to(self, request: httpx.Request) -> httpx.Response:
        """Stripe's answer to `request`, sent again while Stripe says it may be; else raises."""
        call = OwnText(f"{request.method} {request.url.path}")
        for attempt in range(1, constants.STRIPE_ATTEMPTS + 1):
            is_last = attempt == constants.STRIPE_ATTEMPTS
            try:
                answer = self.client.send(request)
            except httpx.TransportError:  # raised by httpx: no connection, or no answer in time
                if is_last:
                    raise
                _log.write(LogEvent.STRIPE_RETRIED, call=call, attempt=attempt, answered=False)
                time.sleep(_wait_after(attempt))
                continue
            if is_last or not _may_try_again(answer):
                # Never there to find: sending it again won't make it so.
                if _error_in(answer).get("code") == _MISSING:  # .get: not every error has one
                    raise NotAtStripe(str(request.url.path))
                return answer.raise_for_status()
            _log.write(
                LogEvent.STRIPE_RETRIED,
                call=call,
                attempt=attempt,
                answered=True,
                status=answer.status_code,
            )
            time.sleep(_wait_after(attempt))
        raise AssertionError("the last attempt returns or raises")


def stripe_api(key: str, *, transport: httpx.BaseTransport | None = None) -> StripeApi:
    """Stripe's API, called with the secret `key`; `transport` is a test's Stripe, if any."""
    client = httpx.Client(
        base_url=_API_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Stripe-Version": constants.STRIPE_API_VERSION,
        },
        timeout=constants.STRIPE_TIMEOUT_S,
        transport=transport,
    )
    return StripeApi(client)


def _one_call(purpose: str) -> str:
    """A key for one call, new each time it's made: its retries send it, nothing else does."""
    return f"squidpdf-{purpose}-{uuid.uuid4().hex}"


def _may_try_again(answer: httpx.Response) -> bool:
    """Whether Stripe's answer is worth another try: its own word on it first, if it says."""
    said = answer.headers.get("Stripe-Should-Retry")  # .get: Stripe says it only when it knows
    if said is not None:
        return said == "true"
    return (
        answer.status_code in _TRY_AGAIN
        or answer.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


def _wait_after(attempt: int) -> float:
    """How long to wait after try `attempt` failed: doubling each time, cut at random."""
    longest = constants.STRIPE_FIRST_WAIT_S * 2 ** (attempt - 1)
    # Not for security: only so retries from many requests don't land together.
    return longest * random.uniform(0.5, 1)


def _paid_until_in(subscriptions: list[dict[str, Any]], *, account: str) -> int | None:
    """The latest end of a paid period among `account`'s own subscriptions; None if none."""
    ends = [
        item["current_period_end"]
        for subscription in subscriptions
        if _pays_for(subscription, account=account)
        for item in subscription["items"]["data"]
    ]
    return max(ends, default=None)


def _pays_for(subscription: dict[str, Any], *, account: str) -> bool:
    """Whether `subscription` is `account`'s own, its month paid, and still being collected."""
    # .get: one made elsewhere, as in Stripe's dashboard, may carry no account.
    is_own = subscription["metadata"].get("account") == account
    # Its collection paused in Stripe: it stays active, but nothing is charged.
    is_collected = subscription.get("pause_collection") is None  # .get: unpaused, it's absent
    return is_own and is_collected and subscription["status"] in _PAID_STATUSES
