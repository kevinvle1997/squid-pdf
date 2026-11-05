"""The emails accounts are sent, and the one door they leave by."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from squidpdf.core import Message, words


@dataclass(frozen=True, slots=True)
class Letter:
    """One email: who it's to, its subject and its words."""

    to: str
    subject: str
    body: str


class Mailer(Protocol):
    """What sends a letter: a mail server, or a test's double noting what it was given."""

    def send(self, letter: Letter) -> None:
        """Send `letter`, or raise MailNotSent saying the server didn't take it."""
        ...


@dataclass(frozen=True, slots=True, eq=False)
class MailController:
    """Every letter an account is sent: written in its reader's words, then sent."""

    mailer: Mailer

    def send_sign_in_link(self, email: str, *, link: str, minutes: int, language: str) -> None:
        """Send `email` the `link` signing it in for `minutes`, or raise MailNotSent."""
        subject = words.render(Message("sign_in_subject"), language)
        body = words.render(
            Message("sign_in_letter", {"link": link, "minutes": minutes}), language
        )
        self.mailer.send(Letter(email, subject=subject, body=body))
