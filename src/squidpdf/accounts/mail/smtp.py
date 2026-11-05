"""Sending a letter through a mail server, over SMTP."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from functools import partial
from typing import Literal

from squidpdf.accounts import constants
from squidpdf.accounts.errors import MailNotSent
from squidpdf.accounts.mail.sending import Letter
from squidpdf.core import ErrorController, Failure

# smtplib's own failures, a refusal or a lost connection, are OSErrors, as a timeout is.
_SMTP_FAILURES = ErrorController((Failure(OSError, MailNotSent),))

# How the connection is secured: TLS from the start (port 465), or upgraded (587).
Security = Literal["tls", "starttls"]


@dataclass(frozen=True, slots=True)
class SmtpMailer:
    """A mail server, reached over TLS and signed in to, sending from one address."""

    host: str
    port: int
    security: Security
    user: str
    password: str  # the mail server's, from the deployment: never logged
    sender: str  # the From: address, which the mail server lets this user send as

    def send(self, letter: Letter) -> None:
        """Send `letter`, or raise MailNotSent saying the server didn't take it."""
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = letter.to
        message["Subject"] = letter.subject
        message.set_content(letter.body)
        _SMTP_FAILURES.result_of(partial(self._deliver, message))

    def _deliver(self, message: EmailMessage) -> None:
        """Hand `message` to the server, signed in."""
        with self._connected() as smtp:
            smtp.login(self.user, self.password)
            smtp.send_message(message)

    def _connected(self) -> smtplib.SMTP:
        """A connection to the server, secured as the deployment says."""
        if self.security == "tls":
            return smtplib.SMTP_SSL(self.host, self.port, timeout=constants.MAIL_TIMEOUT_S)
        smtp = smtplib.SMTP(self.host, self.port, timeout=constants.MAIL_TIMEOUT_S)
        smtp.starttls()
        return smtp
