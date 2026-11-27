from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from squidpdf.accounts.users import UserStore
from squidpdf.core import LogController, LogEvent, Message, words

_log = LogController.for_module(__name__)


@dataclass(frozen=True, slots=True)
class Letter:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, letter: Letter) -> None: ...


@dataclass(frozen=True, slots=True, eq=False)
class MailController:
    """None goes to an address that bounced for good: each bounce counts against the sender."""

    mailer: Mailer
    users: UserStore
    secret: bytes = field(repr=False)

    def send_sign_in_link(self, email: str, *, link: str, minutes: int, language: str) -> None:
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
        return self.users.has_bounced(email, secret=self.secret)
