"""Half set up stops the app starting, saying what's missing, not the first sign-in."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from email.utils import parseaddr
from functools import partial
from pathlib import Path

from squidpdf.accounts import constants
from squidpdf.accounts.billing.stripe_api import StripeApi, stripe_api
from squidpdf.accounts.errors import NotAnEmail
from squidpdf.accounts.mail import MailController, Mailer, Security, SmtpMailer, brevo_mailer
from squidpdf.accounts.users import UserStore, checked_email, open_users

_DATABASE = "SQUIDPDF_DATABASE"
_SECRET = "SQUIDPDF_SECRET"
_SITE = "SQUIDPDF_SITE"
_INVITE_ONLY = "SQUIDPDF_INVITE_ONLY"
_MAIL_FROM = "SQUIDPDF_MAIL_FROM"
_BREVO_KEY = "SQUIDPDF_BREVO_KEY"
_SMTP_HOST = "SQUIDPDF_SMTP_HOST"
_BREVO_WEBHOOK_TOKEN = "SQUIDPDF_BREVO_WEBHOOK_TOKEN"
_SMTP = (
    _SMTP_HOST,
    "SQUIDPDF_SMTP_PORT",
    "SQUIDPDF_SMTP_SECURITY",
    "SQUIDPDF_SMTP_USER",
    "SQUIDPDF_SMTP_PASSWORD",
)
_SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls"}
# No default: the numbers are the business's, so each lives in the deployment's .env alone.
_FREE_DOCUMENTS = "SQUIDPDF_FREE_DOCUMENTS"
_DOCUMENTS_A_MONTH = "SQUIDPDF_DOCUMENTS_A_MONTH"
_ALLOWANCE = (_FREE_DOCUMENTS, _DOCUMENTS_A_MONTH)
_STRIPE_KEY = "SQUIDPDF_STRIPE_KEY"
_STRIPE_WEBHOOK_SECRET = "SQUIDPDF_STRIPE_WEBHOOK_SECRET"
_STRIPE_PRICE = "SQUIDPDF_STRIPE_PRICE"
_STRIPE = (_STRIPE_KEY, _STRIPE_WEBHOOK_SECRET, _STRIPE_PRICE)
_STRIPE_KEY_STARTS = ("sk_", "rk_")
_WEBHOOK_SECRET_STARTS = "whsec_"
_PRICE_STARTS = "price_"
_MIN_SECRET_CHARS = 32  # 192 bits as base64, the least; env.example's command writes 256


class AccountsUnset(RuntimeError):
    """Accounts or metering half set up, or set up with something unusable."""


@dataclass(frozen=True, slots=True)
class Allowance:
    free: int
    monthly: int


def _mail_threads() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(constants.MAIL_THREADS, thread_name_prefix="mail")


def _billing_threads() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(constants.BILLING_THREADS, thread_name_prefix="billing")


@dataclass(frozen=True, slots=True, eq=False)
class Billing:
    stripe: StripeApi
    webhook_secret: bytes = field(repr=False)
    price: str
    threads: ThreadPoolExecutor = field(default_factory=_billing_threads, repr=False)

    async def run[T](self, task: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.threads, partial(copy_context().run, task))


@dataclass(frozen=True, slots=True, eq=False)
class Accounts:
    users: UserStore
    mail: MailController
    secret: bytes = field(repr=False)
    site: str
    invite_only: bool
    allowance: Allowance | None
    webhook_token: bytes | None
    billing: Billing | None
    mail_threads: Executor = field(default_factory=_mail_threads, repr=False)

    async def send_mail(self, task: Callable[[], None]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.mail_threads, partial(copy_context().run, task))

    def send_mail_unawaited(self, task: Callable[[], None]) -> None:
        self.mail_threads.submit(copy_context().run, task)


def named_users() -> UserStore | None:
    named = os.environ.get(_DATABASE)
    if not named:
        return None
    return open_users(Path(named))


def named_secret() -> bytes:
    _require(_SECRET)
    return _secret_in(_SECRET)


def deployed_accounts() -> Accounts | None:
    users = named_users()
    if users is None:
        _refuse_metering_without_accounts()
        return None
    _require(_SECRET, _SITE, _MAIL_FROM)
    secret = _secret_in(_SECRET)
    site = os.environ[_SITE].rstrip("/")
    if not site.startswith("https://"):
        raise AccountsUnset(f"{_SITE} must start with https://, as a link it opens must")
    allowance = _allowance()
    return Accounts(
        users,
        MailController(_mailer(), users, secret=secret),
        secret=secret,
        site=site,
        invite_only=bool(os.environ.get(_INVITE_ONLY)),
        allowance=allowance,
        webhook_token=_webhook_token(),
        billing=_billing(allowance),
    )


def _secret_in(name: str) -> bytes:
    value = os.environ[name]
    if len(value) < _MIN_SECRET_CHARS:
        raise AccountsUnset(f"{name} is shorter than {_MIN_SECRET_CHARS} characters")
    return value.encode()


def _webhook_token() -> bytes | None:
    if not os.environ.get(_BREVO_WEBHOOK_TOKEN):
        return None
    return _secret_in(_BREVO_WEBHOOK_TOKEN)


def _allowance() -> Allowance | None:
    named = [os.environ.get(name) for name in _ALLOWANCE]
    if not any(named):
        return None
    if not all(named):
        raise AccountsUnset(f"metering needs both {' and '.join(_ALLOWANCE)}, or neither")
    free, monthly = (_count_in(name) for name in _ALLOWANCE)
    return Allowance(free, monthly)


def _refuse_metering_without_accounts() -> None:
    if any(os.environ.get(name) for name in _ALLOWANCE):
        raise AccountsUnset(f"metering asks for an account, so it needs {_DATABASE} too")
    if any(os.environ.get(name) for name in _STRIPE):
        raise AccountsUnset(f"an account is what pays, so payments need {_DATABASE} too")


def _billing(allowance: Allowance | None) -> Billing | None:
    named = [os.environ.get(name) for name in _STRIPE]
    if not any(named):
        return None
    if not all(named):
        raise AccountsUnset(f"payments need all of {', '.join(_STRIPE)}, or none")
    if allowance is None:
        raise AccountsUnset(f"payment lifts a limit, so it needs {' and '.join(_ALLOWANCE)}")
    key, webhook_secret, price = (os.environ[name] for name in _STRIPE)
    # No value goes in the message: the key and the secret are secrets.
    if not key.startswith(_STRIPE_KEY_STARTS):
        raise AccountsUnset(f"{_STRIPE_KEY} is no Stripe secret key, which starts sk_ or rk_")
    if not webhook_secret.startswith(_WEBHOOK_SECRET_STARTS):
        raise AccountsUnset(f"{_STRIPE_WEBHOOK_SECRET} is no signing secret, as whsec_...")
    if not price.startswith(_PRICE_STARTS):
        raise AccountsUnset(f"{_STRIPE_PRICE} is no Stripe price, as price_...")
    return Billing(stripe_api(key), webhook_secret=webhook_secret.encode(), price=price)


def _count_in(name: str) -> int:
    value = os.environ[name]
    if not value.isdigit():
        raise AccountsUnset(f"{name} is {value!r}, not a number of documents")
    return int(value)


def _mailer() -> Mailer:
    sender = _sender()
    key = os.environ.get(_BREVO_KEY)
    if not key:
        _require(*_SMTP)
        return _smtp_mailer(sender)
    # Both named: which sends is the deployer's to say, not ours to guess.
    if os.environ.get(_SMTP_HOST):
        raise AccountsUnset(f"{_BREVO_KEY} and {_SMTP_HOST} both name a way to send mail")
    return brevo_mailer(key, sender=sender)


def _sender() -> str:
    sender = os.environ[_MAIL_FROM]
    try:
        checked_email(parseaddr(sender)[1])
    except NotAnEmail:
        raise AccountsUnset(f"{_MAIL_FROM} is {sender!r}, not one address") from None
    return sender


def _smtp_mailer(sender: str) -> SmtpMailer:
    host, port, security, user, password = (os.environ[name] for name in _SMTP)
    secured = _SECURITIES.get(security)
    if secured is None:
        raise AccountsUnset(f"SQUIDPDF_SMTP_SECURITY is {security!r}, not tls or starttls")
    if not port.isdigit():
        raise AccountsUnset(f"SQUIDPDF_SMTP_PORT is {port!r}, not a port number")
    return SmtpMailer(
        host, int(port), security=secured, user=user, password=password, sender=sender
    )


def _require(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise AccountsUnset(f"{_DATABASE} is set, so accounts need {', '.join(missing)} too")
