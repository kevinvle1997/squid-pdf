"""Sending a letter through Brevo's API, over HTTPS, rather than a mail server's SMTP."""

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

_SEND_URL = "https://api.brevo.com/v3/smtp/email"  # Brevo's address for one transactional email
# Brevo's answers that its account sends no more for now: past its sending limit, or too fast.
_LIMIT_STATUSES = frozenset({HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.TOO_MANY_REQUESTS})


def _problem_of_refusal(failure: Exception) -> Problem:
    """What Brevo's refusal means: its limit reached, or the email not sent, and its why."""
    refused = cast(httpx.HTTPStatusError, failure)  # _BREVO_FAILURES hands it only these
    status = refused.response.status_code
    # Its code alone: the message beside it may name the address, which no log keeps.
    debug = f"Brevo answered {status} ({_code_in(refused.response)})"
    if status in _LIMIT_STATUSES:
        return MailLimitReached(debug=debug)
    return MailNotSent(debug=debug)


def _code_in(answer: httpx.Response) -> str:
    """Brevo's word for why it refused, as its JSON names it; "no code" where it names none."""
    try:
        said = answer.json()
    except ValueError:  # raised by the JSON reader: a proxy's page, not Brevo's answer
        return "no code"
    # .get: its JSON names the code, but a proxy's may not.
    return str(said.get("code", "no code")) if isinstance(said, dict) else "no code"


# Brevo's failures. Its refusal first: an HTTPStatusError is an HTTPError too.
_BREVO_FAILURES = ErrorController(
    (
        Failure(httpx.HTTPStatusError, _problem_of_refusal),
        Failure(httpx.HTTPError, MailNotSent),
    )
)


@dataclass(frozen=True, slots=True, eq=False)
class BrevoMailer:
    """Brevo's API, its key in each request, sending as one sender; made by `brevo_mailer`."""

    client: httpx.Client  # carries the key: never logged
    sender_name: str  # empty where the From: address has no name
    sender_email: str

    def send(self, letter: Letter) -> None:
        """Send `letter`, or raise MailNotSent, or MailLimitReached, saying why not."""
        _BREVO_FAILURES.result_of(partial(self._post, letter))

    def _post(self, letter: Letter) -> None:
        """Hand `letter` to Brevo; raise if it doesn't take it."""
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
    """A mailer sending as `sender`, a From: address, through the Brevo account `key` opens.

    `transport` is a test's stand-in for Brevo; the app passes none.
    """
    name, email = parseaddr(sender)
    client = httpx.Client(
        headers={"api-key": key, "accept": "application/json"},
        timeout=constants.MAIL_TIMEOUT_S,
        transport=transport,
    )
    return BrevoMailer(client, sender_name=name, sender_email=email)
