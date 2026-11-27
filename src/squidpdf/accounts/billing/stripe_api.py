"""A retry sends the same request, a POST's idempotency key too, so Stripe does it once."""

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
from squidpdf.accounts.types import OwnSubscription, Subscriptions
from squidpdf.core import ErrorController, Failure, LogController, LogEvent, OwnText, Problem

_log = LogController.for_module(__name__)

_API_URL = "https://api.stripe.com"
# Worth a retry when Stripe doesn't say: a clash on the same object or key, or too many calls.
_TRY_AGAIN = frozenset({HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS})
_PAID_STATUSES = frozenset({"active", "trialing"})
_ENDED_STATUSES = frozenset({"canceled", "incomplete_expired"})
_MISSING = "resource_missing"
_NO_CODE = "no code"
_PAID_PATH = "/?billing=paid"


class NotAtStripe(Exception):
    """Stripe hasn't got what a call names: a customer deleted, or made in test mode."""


def _problem_of_refusal(failure: Exception) -> Problem:
    refused = cast(httpx.HTTPStatusError, failure)
    status = refused.response.status_code
    # Its code alone: the message beside it may quote what was sent, an address included.
    return PaymentsUnavailable(debug=f"Stripe answered {status} ({_code_in(refused.response)})")


def _code_in(answer: httpx.Response) -> str:
    error = _error_in(answer)
    return f"{error.get('type', _NO_CODE)}/{error.get('code', _NO_CODE)}"


def _error_in(answer: httpx.Response) -> dict[str, Any]:
    try:
        said = answer.json()
    except ValueError:
        return {}
    error = said.get("error") if isinstance(said, dict) else None
    return error if isinstance(error, dict) else {}


# Its refusal first: an HTTPStatusError is an HTTPError too.
_STRIPE_FAILURES = ErrorController(
    (
        Failure(httpx.HTTPStatusError, _problem_of_refusal),
        Failure(httpx.HTTPError, PaymentsUnavailable),
    )
)


@dataclass(frozen=True, slots=True, eq=False)
class StripeApi:
    client: httpx.Client  # never logged

    def new_customer(self, *, account: str, email: str) -> str:
        form = {"email": email, "metadata[account]": account}
        # The call's key, not the account's: Stripe keeps a key's answer a day, an error too.
        return self._post("/v1/customers", form=form, key=_one_call("customer"))["id"]

    def checkout_url(self, *, customer: str, account: str, price: str, site: str) -> str:
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

    def billing_portal_url(self, *, customer: str, site: str) -> str:
        form = {"customer": customer, "return_url": f"{site}/"}
        made = self._post("/v1/billing_portal/sessions", form=form, key=_one_call("portal"))
        return made["url"]

    def subscriptions(self, customer: str, *, account: str) -> Subscriptions:
        # A hundred, Stripe's most a page: a customer never has that many subscriptions.
        params = {"customer": customer, "status": "all", "limit": "100"}
        try:
            listed = self._call(
                self.client.build_request("GET", "/v1/subscriptions", params=params)
            )
        except NotAtStripe:
            return Subscriptions(paid_until=None, unended=0, own_unended=())
        listing = listed["data"]
        billing = [each for each in listing if each["status"] not in _ENDED_STATUSES]
        own = [each for each in billing if _is_own(each, account=account)]
        return Subscriptions(
            paid_until=_paid_until_in(listing, account=account),
            unended=len(billing),
            own_unended=tuple(
                OwnSubscription(each["id"], created=each["created"]) for each in own
            ),
        )

    def customer_of_charge(self, charge: str) -> str | None:
        try:
            made = self._call(self.client.build_request("GET", f"/v1/charges/{charge}"))
        except NotAtStripe:
            return None
        return made["customer"]

    def cancel_subscription(self, subscription: str, *, why: str) -> None:
        """Stripe has no subscription it ended: a try whose answer was lost finds it gone."""
        path = f"/v1/subscriptions/{subscription}"
        form = {"cancellation_details[comment]": why}
        try:
            self._call(self.client.build_request("DELETE", path, data=form))
        except NotAtStripe:
            _log.write(LogEvent.STRIPE_CANCELLED, call=OwnText(path), already=True)
            return
        _log.write(LogEvent.STRIPE_CANCELLED, call=OwnText(path), already=False)

    def _post(self, path: str, *, form: dict[str, str], key: str) -> dict[str, Any]:
        headers = {"Idempotency-Key": key}
        made = self._call(self.client.build_request("POST", path, data=form, headers=headers))
        _log.write(LogEvent.STRIPE_POSTED, call=OwnText(path), key=OwnText(key))
        return made

    def _call(self, request: httpx.Request) -> dict[str, Any]:
        answer = _STRIPE_FAILURES.result_of(partial(self._answer_to, request))
        return answer.json()

    def _answer_to(self, request: httpx.Request) -> httpx.Response:
        call = OwnText(f"{request.method} {request.url.path}")
        for attempt in range(1, constants.STRIPE_ATTEMPTS + 1):
            is_last = attempt == constants.STRIPE_ATTEMPTS
            try:
                answer = self.client.send(request)
            except httpx.TransportError:
                if is_last:
                    raise
                _log.write(LogEvent.STRIPE_RETRIED, call=call, attempt=attempt, answered=False)
                time.sleep(_wait_after(attempt))
                continue
            if is_last or not _may_try_again(answer):
                if _error_in(answer).get("code") == _MISSING:
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
    return f"squidpdf-{purpose}-{uuid.uuid4().hex}"


def _may_try_again(answer: httpx.Response) -> bool:
    said = answer.headers.get("Stripe-Should-Retry")
    if said is not None:
        return said == "true"
    return (
        answer.status_code in _TRY_AGAIN
        or answer.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


def _wait_after(attempt: int) -> float:
    longest = constants.STRIPE_FIRST_WAIT_S * 2 ** (attempt - 1)
    # Not for security: only so retries from many requests don't land together.
    return longest * random.uniform(0.5, 1)


def _paid_until_in(subscriptions: list[dict[str, Any]], *, account: str) -> int | None:
    ends = [
        item["current_period_end"]
        for subscription in subscriptions
        if _pays_for(subscription, account=account)
        for item in subscription["items"]["data"]
    ]
    return max(ends, default=None)


def _pays_for(subscription: dict[str, Any], *, account: str) -> bool:
    # Its collection paused in Stripe: it stays active, but nothing is charged.
    is_collected = subscription.get("pause_collection") is None
    is_paid = subscription["status"] in _PAID_STATUSES
    return _is_own(subscription, account=account) and is_collected and is_paid


def _is_own(subscription: dict[str, Any], *, account: str) -> bool:
    return subscription["metadata"].get("account") == account
