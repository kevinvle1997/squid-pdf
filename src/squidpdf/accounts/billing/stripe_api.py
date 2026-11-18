"""Stripe's API over HTTPS: each call tried again with care, and what Stripe says is paid.

A call is tried again only where Stripe says it may be, or gave no answer, and each try
sends the very same request: a POST's idempotency key with it, so Stripe does it once.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from typing import Any, cast

import httpx

from squidpdf.accounts import constants
from squidpdf.accounts.errors import PaymentsUnavailable
from squidpdf.core import ErrorController, Failure, LogController, LogEvent, OwnText, Problem

_log = LogController.for_module(__name__)

_API_URL = "https://api.stripe.com"  # Stripe's API, every path below it
# Answers that are worth another try when Stripe doesn't say: a clash with a call under way
# on the same object or key, or too many calls at once. A 5xx is too.
_TRY_AGAIN = frozenset({HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS})
# A subscription in these has its month paid; past_due, unpaid and the rest have stopped.
_PAID_STATUSES = frozenset({"active", "trialing"})
_NO_CODE = "no code"  # what the log says where an answer names no error


def _problem_of_refusal(failure: Exception) -> Problem:
    """Stripe's last refusal, its status and its error's code kept for the log."""
    refused = cast(httpx.HTTPStatusError, failure)  # _STRIPE_FAILURES hands it only these
    status = refused.response.status_code
    # Its code alone: the message beside it may quote what was sent, an address included.
    return PaymentsUnavailable(debug=f"Stripe answered {status} ({_code_in(refused.response)})")


def _code_in(answer: httpx.Response) -> str:
    """Stripe's words for why it refused, its error's type and code; _NO_CODE for none."""
    try:
        said = answer.json()
    except ValueError:  # raised by the JSON reader: a proxy's page, not Stripe's answer
        return _NO_CODE
    error = said.get("error") if isinstance(said, dict) else None  # .get: a proxy's may not
    if not isinstance(error, dict):
        return _NO_CODE
    # .get: Stripe names a code for some errors, not all.
    return f"{error.get('type', _NO_CODE)}/{error.get('code', _NO_CODE)}"


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

    def paid_until(self, customer: str) -> int | None:
        """Until when `customer`'s month is paid, as its subscriptions stand now; None if not.

        Raises PaymentsUnavailable when Stripe can't be asked.
        """
        # A hundred, Stripe's most a page: a customer never has that many subscriptions.
        listed = self._call(
            "GET",
            "/v1/subscriptions",
            params={"customer": customer, "status": "all", "limit": 100},
        )
        return _paid_until_in(listed["data"])

    def _call(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Stripe's answer to one call, after up to STRIPE_ATTEMPTS tries; else it raises."""
        request = self.client.build_request(method, path, params=params)
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


def _paid_until_in(subscriptions: list[dict[str, Any]]) -> int | None:
    """The latest end of a paid period among `subscriptions`' items; None if none is paid."""
    ends = [
        item["current_period_end"]
        for subscription in subscriptions
        if subscription["status"] in _PAID_STATUSES
        for item in subscription["items"]["data"]
    ]
    return max(ends, default=None)
