"""The emails accounts are sent, and the one door they leave by."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from squidpdf.accounts.users import UserStore
from squidpdf.core import LogController, LogEvent, Message, words

_log = LogController.for_module(__name__)


@dataclass(frozen=True, slots=True)
class Letter:
    """One email: who it's to, its subject and its words."""

    to: str
    subject: str
    body: str


class Mailer(Protocol):
    """What sends an email: SMTP, Brevo's API, or a test's double noting what it was given."""

    def send(self, letter: Letter) -> None:
        """Send `letter`, or raise the Problem saying why it wasn't sent."""
        ...


@dataclass(frozen=True, slots=True, eq=False)
class MailController:
    """Every letter an account is sent: written in its reader's words, then sent.

    Nothing goes to an address whose mail came back as never deliverable: each bounce
    counts against the sender with every mail server. Inviting them again clears it.
    """

    mailer: Mailer
    users: UserStore  # who has bounced

    def send_sign_in_link(self, email: str, *, link: str, minutes: int, language: str) -> None:
        """Send `email` the `link` signing it in for `minutes`, or raise why it wasn't.

        Reads the table: a coroutine calls it in a thread.
        """
        # Said as any address is, so no one learns which bounced; the log keeps it.
        if self._has_bounced(email):
            _log.write(LogEvent.MAIL_TO_BOUNCED_SKIPPED)
            return
        subject = words.render(Message("sign_in_subject"), language)
        body = words.render(
            Message("sign_in_letter", {"link": link, "minutes": minutes}), language
        )
        self.mailer.send(Letter(email, subject=subject, body=body))

    def _has_bounced(self, email: str) -> bool:
        """Whether mail to `email` came back as never deliverable; no account, none has."""
        account = self.users.find(email)
        return account is not None and account.bounced_at is not None
