from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from functools import partial
from http import HTTPStatus
from typing import cast

import httpx

from squidpdf.accounts import constants
from squidpdf.accounts.errors import MailLimitReached, MailNotSent
from squidpdf.accounts.mail.sending import Letter
from squidpdf.core import ErrorController, Failure, Problem

_SEND_URL = "https://api.brevo.com/v3/smtp/email"
_LIMIT_STATUSES = frozenset({HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.TOO_MANY_REQUESTS})
_NO_CODE = "no code"


def _problem_of_refusal(failure: Exception) -> Problem:
    refused = cast(httpx.HTTPStatusError, failure)
    status = refused.response.status_code
    # Its code alone: the message beside it may name the address, which no log keeps.
    debug = f"Brevo answered {status} ({_code_in(refused.response)})"
    if status in _LIMIT_STATUSES:
        return MailLimitReached(debug=debug)
    return MailNotSent(debug=debug)


def _code_in(answer: httpx.Response) -> str:
    try:
        said = answer.json()
    except ValueError:
        return _NO_CODE
    return str(said.get("code", _NO_CODE)) if isinstance(said, dict) else _NO_CODE


# Brevo's failures. Its refusal first: an HTTPStatusError is an HTTPError too.
_BREVO_FAILURES = ErrorController(
    (
        Failure(httpx.HTTPStatusError, _problem_of_refusal),
        Failure(httpx.HTTPError, MailNotSent),
    )
)


@dataclass(frozen=True, slots=True, eq=False)
class BrevoMailer:
    client: httpx.Client  # never logged
    sender_name: str
    sender_email: str

    def send(self, letter: Letter) -> None:
        _BREVO_FAILURES.result_of(partial(self._post, letter))

    def _post(self, letter: Letter) -> None:
        sender = {"email": self.sender_email} | (
            {"name": self.sender_name} if self.sender_name else {}
        )
        sent = self.client.post(
            _SEND_URL,
            json={
                "sender": sender,
                "to": [{"email": letter.to}],
                "subject": letter.subject,
                "textContent": letter.body,
            },
        )
        sent.raise_for_status()


def brevo_mailer(
    key: str, *, sender: str, transport: httpx.BaseTransport | None = None
) -> BrevoMailer:
    name, email = parseaddr(sender)
    client = httpx.Client(
        headers={"api-key": key, "accept": "application/json"},
        timeout=constants.MAIL_TIMEOUT_S,
        transport=transport,
    )
    return BrevoMailer(client, sender_name=name, sender_email=email)
