from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from http import HTTPStatus

from squidpdf.accounts.deployment import Accounts
from squidpdf.accounts.types import MailEvent
from squidpdf.accounts.users import checked_email
from squidpdf.core import LogController, LogEvent, Reply

_log = LogController.for_module(__name__)

_HARD_BOUNCE = "hard_bounce"


@dataclass(frozen=True, slots=True, eq=False)
class BounceController:
    accounts: Accounts

    async def note(self, sent: MailEvent | list[MailEvent]) -> Reply[bytes]:
        # Brevo sends one event, or a list where its webhook batches them.
        events = sent if isinstance(sent, list) else [sent]
        bounced = [
            checked_email(event.email) for event in events if event.event == _HARD_BOUNCE
        ]
        now = int(time.time())
        for email in bounced:
            had_account = await asyncio.to_thread(
                self.accounts.users.note_bounce, email, secret=self.accounts.secret, now=now
            )
            _log.write(LogEvent.MAIL_BOUNCE_NOTED, had_account=had_account)
        return Reply(b"", {}, HTTPStatus.NO_CONTENT)


def is_brevo(accounts: Accounts, authorization: str | None) -> bool:
    if accounts.webhook_token is None or authorization is None:
        return False
    # Compared in constant time, so the token can't be guessed a letter at a time.
    return hmac.compare_digest(authorization.encode(), b"Bearer " + accounts.webhook_token)
