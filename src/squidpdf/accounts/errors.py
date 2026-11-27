from __future__ import annotations

from squidpdf.core import Problem


class NotAnEmail(Problem):
    type = "not_an_email"
    status = 422


class LinkExpired(Problem):
    type = "link_expired"
    status = 400


class MailNotSent(Problem):
    type = "mail_not_sent"
    status = 503


class MailLimitReached(Problem):
    type = "mail_limit_reached"
    status = 503


class PaymentsUnavailable(Problem):
    type = "payments_unavailable"
    status = 503


class NoPayments(Problem):
    type = "no_payments"
    status = 404


class SignInToPay(Problem):
    type = "sign_in_to_pay"
    status = 401


class AlreadyUnlimited(Problem):
    type = "already_unlimited"
    status = 409


class AlreadySubscribed(Problem):
    type = "already_subscribed"
    status = 409


class NoBilling(Problem):
    type = "no_billing"
    status = 404


class NoAccounts(Problem):
    type = "no_accounts"
    status = 404


class NotInvited(Problem):
    type = "not_invited"
    status = 403


class InvitedOnly(Problem):
    type = "invited_only"
    status = 401


class FreeDocumentsUsed(Problem):
    type = "free_documents_used"
    status = 403

    def __init__(self, *, monthly: int) -> None:
        super().__init__(monthly=monthly)


class MonthlyDocumentsUsed(Problem):
    type = "monthly_documents_used"
    status = 403

    def __init__(self, *, monthly: int) -> None:
        super().__init__(monthly=monthly)
