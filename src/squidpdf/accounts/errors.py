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


class PaymentsUnavailable(Problem):
    """Stripe didn't answer, or refused, after every try: nothing was charged."""

    type = "payments_unavailable"
    status = 503


class NoPayments(Problem):
    """This server takes no payment, as a self-hosted copy takes none."""

    type = "no_payments"
    status = 404


class SignInToPay(Problem):
    """No account is signed in: payment lifts an account's limit, so one must be."""

    type = "sign_in_to_pay"
    status = 401


class AlreadyUnlimited(Problem):
    """The account has no document limit already: a friend's, or a month paid."""

    type = "already_unlimited"
    status = 409


class NeverPaid(Problem):
    """The account has never gone to pay, so Stripe has no billing of it to manage."""

    type = "never_paid"
    status = 404


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
