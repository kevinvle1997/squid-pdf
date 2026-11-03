"""What can go wrong signing in."""

from __future__ import annotations

from squidpdf.core import Problem


class NotAnEmail(Problem):
    """What was typed can't be an email address: no link could reach it."""

    type = "not_an_email"
    status = 422


class LinkExpired(Problem):
    """A sign-in link past its time, or not whole: its signature doesn't hold."""

    type = "link_expired"
    status = 400


class MailNotSent(Problem):
    """The mail server didn't take the link: down, refusing, or too slow."""

    type = "mail_not_sent"
    status = 503


class NoAccounts(Problem):
    """This server keeps no accounts, as a self-hosted copy keeps none."""

    type = "no_accounts"
    status = 404


class NotInvited(Problem):
    """A link for an address no one invited, on a server open to invited people only."""

    type = "not_invited"
    status = 403


class InvitedOnly(Problem):
    """Opening a document needs signing in, on a server open to invited people only."""

    type = "invited_only"
    status = 401
