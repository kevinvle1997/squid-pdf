"""What the browser is told about accounts, and what Brevo's webhook tells the server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class SignedIn(TypedDict):
    """The account this browser is signed in to."""

    email: str
    is_unlimited: bool  # uses as many documents as they like


class AccountState(TypedDict):
    """Whether this server offers accounts, and which this browser is signed in to."""

    offered: bool  # false on a server that keeps none, as a self-hosted copy
    invite_only: bool  # only invited people may sign in, and only they open documents
    signed_in: SignedIn | None


class LinkSent(TypedDict):
    """A sign-in link is on its way: where, in the reader's words."""

    sentence: str


@dataclass(frozen=True, slots=True)
class MailEvent:
    """What Brevo's webhook says happened to an email: its word for it, and the address."""

    event: str
    email: str
