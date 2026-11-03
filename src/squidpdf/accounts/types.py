"""What the browser is told about accounts."""

from __future__ import annotations

from typing import TypedDict


class SignedIn(TypedDict):
    """The account this browser is signed in to."""

    email: str
    is_unlimited: bool  # uses as many documents as they like


class AccountState(TypedDict):
    """Whether this server offers accounts, and which this browser is signed in to."""

    offered: bool  # false on a server that keeps none, as a self-hosted copy
    signed_in: SignedIn | None


class LinkSent(TypedDict):
    """A sign-in link is on its way: where, in the reader's words."""

    sentence: str
