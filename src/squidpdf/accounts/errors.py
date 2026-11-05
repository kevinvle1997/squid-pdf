"""What can go wrong signing in."""

from __future__ import annotations

from squidpdf.core import Problem


class NotAnEmail(Problem):
    """What was typed can't be an email address: no link could reach it."""

    type = "not_an_email"
    status = 422


class LinkExpired(Problem):
    """A sign-in link past its time, used already, or not whole, its signature not holding."""

    type = "link_expired"
    status = 400


class MailNotSent(Problem):
    """The mailer didn't take the link: down, refusing, or too slow."""

    type = "mail_not_sent"
    status = 503


class MailLimitReached(Problem):
    """The mailer sends no more for now: past its sending limit, or asked too fast."""

    type = "mail_limit_reached"
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


class FreeDocumentsUsed(Problem):
    """A browser with no account has opened the documents it may: signing in opens more."""

    type = "free_documents_used"
    status = 403

    def __init__(self, *, monthly: int) -> None:
        """Name how many an account opens a month."""
        super().__init__(monthly=monthly)


class MonthlyDocumentsUsed(Problem):
    """An account has opened this month's documents: more open next month."""

    type = "monthly_documents_used"
    status = 403

    def __init__(self, *, monthly: int) -> None:
        """Name how many it opens a month."""
        super().__init__(monthly=monthly)
