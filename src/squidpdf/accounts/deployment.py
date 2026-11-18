"""Accounts as the deployment configures them, from its environment (env.example).

None of it is set on a self-hosted copy, which then keeps no accounts. Naming the database
but not the rest, or an allowance or payments with no database or only half set, stops the
app starting, saying what's missing, rather than failing on the first sign-in or payment.
"""

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

# The SQLite file accounts are kept in; unset or empty, the server keeps none.
_DATABASE = "SQUIDPDF_DATABASE"
# Signs every link and sign-in: generated, never chosen (env.example says how).
_SECRET = "SQUIDPDF_SECRET"
_SITE = "SQUIDPDF_SITE"  # the https:// address a link opens, as https://pdf.example.com
# Set to anything, only those `squidpdf invite` let in sign in and open documents.
_INVITE_ONLY = "SQUIDPDF_INVITE_ONLY"
_MAIL_FROM = "SQUIDPDF_MAIL_FROM"  # the address links come from, however they're sent
# Set, links go through Brevo's API, and the SMTP lines stay empty.
_BREVO_KEY = "SQUIDPDF_BREVO_KEY"
_SMTP_HOST = "SQUIDPDF_SMTP_HOST"  # names the mail server; set, the SMTP lines send
# Set, Brevo's webhook may say which addresses bounce, sending it as its bearer token.
_BREVO_WEBHOOK_TOKEN = "SQUIDPDF_BREVO_WEBHOOK_TOKEN"
_SMTP = (
    _SMTP_HOST,
    "SQUIDPDF_SMTP_PORT",
    "SQUIDPDF_SMTP_SECURITY",
    "SQUIDPDF_SMTP_USER",
    "SQUIDPDF_SMTP_PASSWORD",
)
# Each way the environment may name how the connection is secured.
_SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls"}
# How many documents a browser opens without an account, and an account opens a month. No
# default: the numbers are the business's, so each lives in the deployment's .env alone.
_FREE_DOCUMENTS = "SQUIDPDF_FREE_DOCUMENTS"
_DOCUMENTS_A_MONTH = "SQUIDPDF_DOCUMENTS_A_MONTH"
_ALLOWANCE = (_FREE_DOCUMENTS, _DOCUMENTS_A_MONTH)
# Stripe, where the deployment takes payment: its secret key, its webhook's signing secret and
# the monthly price paid, all or none, and only with an allowance for payment to lift.
_STRIPE_KEY = "SQUIDPDF_STRIPE_KEY"
_STRIPE_WEBHOOK_SECRET = "SQUIDPDF_STRIPE_WEBHOOK_SECRET"
_STRIPE_PRICE = "SQUIDPDF_STRIPE_PRICE"
_STRIPE = (_STRIPE_KEY, _STRIPE_WEBHOOK_SECRET, _STRIPE_PRICE)
# How each begins as Stripe makes them: a secret or restricted key, a signing secret, a price.
_STRIPE_KEY_STARTS = ("sk_", "rk_")
_WEBHOOK_SECRET_STARTS = "whsec_"
_PRICE_STARTS = "price_"
_MIN_SECRET_CHARS = 32  # 192 bits as base64, the least; env.example's command writes 256


class AccountsUnset(RuntimeError):
    """Accounts or metering half set up, or set up with something unusable."""


@dataclass(frozen=True, slots=True)
class Allowance:
    """How many documents a browser opens: without an account, then with one, a month."""

    free: int  # documents a browser opens without an account
    monthly: int


def _mail_threads() -> ThreadPoolExecutor:
    """The threads sign-in links are sent on, MAIL_THREADS of them."""
    return ThreadPoolExecutor(constants.MAIL_THREADS, thread_name_prefix="mail")


def _billing_threads() -> ThreadPoolExecutor:
    """The threads Stripe is called on, BILLING_THREADS of them."""
    return ThreadPoolExecutor(constants.BILLING_THREADS, thread_name_prefix="billing")


@dataclass(frozen=True, slots=True, eq=False)
class Billing:
    """What taking payment needs: Stripe, its webhook's secret, the price, its own threads."""

    stripe: StripeApi
    webhook_secret: bytes = field(repr=False)  # signs each event Stripe's webhook sends
    price: str  # Stripe's monthly price an account pays, as price_...
    # Stripe is called on threads of its own: a Stripe that hangs holds these, never the
    # threads every upload and read share.
    threads: ThreadPoolExecutor = field(default_factory=_billing_threads, repr=False)

    async def run[T](self, task: Callable[[], T]) -> T:
        """What `task()` returns, run on billing's threads, the request's id carried along."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.threads, partial(copy_context().run, task))


@dataclass(frozen=True, slots=True, eq=False)
class Accounts:
    """What accounts need: the table, the mail, the secret and address, who may, how many.

    And the token Brevo's webhook sends, to say which addresses bounce.
    """

    users: UserStore
    mail: MailController
    secret: bytes = field(repr=False)  # signs links and the signed-in cookie
    site: str  # where a link sends its reader, with no slash at the end
    invite_only: bool  # only those `squidpdf invite` let in sign in and open documents
    allowance: Allowance | None  # None where the deployment meters nothing
    webhook_token: bytes | None  # what Brevo's webhook sends; None where it has none
    billing: Billing | None  # None where the deployment takes no payment
    # Letters go out on threads of their own: a mail server that hangs holds these, never
    # the threads every upload and read share.
    mail_threads: Executor = field(default_factory=_mail_threads, repr=False)

    async def send_mail(self, task: Callable[[], None]) -> None:
        """Run `task` on mail's threads and wait for it, the request's id carried along."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self.mail_threads, partial(copy_context().run, task))

    def send_mail_unawaited(self, task: Callable[[], None]) -> None:
        """Start `task` on mail's threads and go on, the request's id carried along."""
        self.mail_threads.submit(copy_context().run, task)


def named_users() -> UserStore | None:
    """The users table in the file `SQUIDPDF_DATABASE` names; None where it names none."""
    named = os.environ.get(_DATABASE)  # .get: a self-hosted copy names none
    if not named:
        return None
    return open_users(Path(named))


def named_secret() -> bytes:
    """The secret `SQUIDPDF_SECRET` holds, for the command line; AccountsUnset if none."""
    _require(_SECRET)
    return _secret_in(_SECRET)


def deployed_accounts() -> Accounts | None:
    """Accounts as the environment sets them up; None where it names no database."""
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
        invite_only=bool(os.environ.get(_INVITE_ONLY)),  # .get: unset, anyone may sign in
        allowance=allowance,
        webhook_token=_webhook_token(),
        billing=_billing(allowance),
    )


def _secret_in(name: str) -> bytes:
    """The secret the variable `name` holds, or AccountsUnset if it's too short to be one."""
    value = os.environ[name]
    if len(value) < _MIN_SECRET_CHARS:
        raise AccountsUnset(f"{name} is shorter than {_MIN_SECRET_CHARS} characters")
    return value.encode()


def _webhook_token() -> bytes | None:
    """The token Brevo's webhook sends; None where the environment sets none."""
    if not os.environ.get(_BREVO_WEBHOOK_TOKEN):  # .get: unset, no webhook is heard
        return None
    return _secret_in(_BREVO_WEBHOOK_TOKEN)


def _allowance() -> Allowance | None:
    """The allowance the environment sets; None where it sets neither number."""
    named = [os.environ.get(name) for name in _ALLOWANCE]  # .get: unset meters nothing
    if not any(named):
        return None
    if not all(named):
        raise AccountsUnset(f"metering needs both {' and '.join(_ALLOWANCE)}, or neither")
    free, monthly = (_count_in(name) for name in _ALLOWANCE)
    return Allowance(free, monthly)


def _refuse_metering_without_accounts() -> None:
    """Raise AccountsUnset if an allowance or payments are set with no database for accounts."""
    if any(os.environ.get(name) for name in _ALLOWANCE):  # .get: unset is the usual case
        raise AccountsUnset(f"metering asks for an account, so it needs {_DATABASE} too")
    if any(os.environ.get(name) for name in _STRIPE):  # .get: unset is the usual case
        raise AccountsUnset(f"an account is what pays, so payments need {_DATABASE} too")


def _billing(allowance: Allowance | None) -> Billing | None:
    """Stripe, where the environment sets its key, secret and price; None where none."""
    named = [os.environ.get(name) for name in _STRIPE]  # .get: unset takes no payment
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
    """The number of documents the variable `name` sets, or AccountsUnset if it isn't one."""
    value = os.environ[name]
    if not value.isdigit():
        raise AccountsUnset(f"{name} is {value!r}, not a number of documents")
    return int(value)


def _mailer() -> Mailer:
    """What sends the links: Brevo's API where the environment sets its key, else SMTP."""
    sender = _sender()
    key = os.environ.get(_BREVO_KEY)  # .get: unset, links go by SMTP
    if not key:
        _require(*_SMTP)
        return _smtp_mailer(sender)
    # Both named: which sends is the deployer's to say, not ours to guess.
    if os.environ.get(_SMTP_HOST):  # .get: unset is the case with a key
        raise AccountsUnset(f"{_BREVO_KEY} and {_SMTP_HOST} both name a way to send mail")
    return brevo_mailer(key, sender=sender)


def _sender() -> str:
    """The From: address the environment names; AccountsUnset unless it holds one address."""
    sender = os.environ[_MAIL_FROM]
    try:
        checked_email(parseaddr(sender)[1])
    except NotAnEmail:  # raised by checked_email: no address, or two, or a name unbracketed
        raise AccountsUnset(f"{_MAIL_FROM} is {sender!r}, not one address") from None
    return sender


def _smtp_mailer(sender: str) -> SmtpMailer:
    """The mail server the environment names, sending as `sender`."""
    host, port, security, user, password = (os.environ[name] for name in _SMTP)
    secured = _SECURITIES.get(security)  # None for a word that names neither
    if secured is None:
        raise AccountsUnset(f"SQUIDPDF_SMTP_SECURITY is {security!r}, not tls or starttls")
    if not port.isdigit():
        raise AccountsUnset(f"SQUIDPDF_SMTP_PORT is {port!r}, not a port number")
    return SmtpMailer(
        host, int(port), security=secured, user=user, password=password, sender=sender
    )


def _require(*names: str) -> None:
    """Raise AccountsUnset naming each of `names` the environment leaves unset or empty."""
    missing = [name for name in names if not os.environ.get(name)]  # .get: unset is the case
    if missing:
        raise AccountsUnset(f"{_DATABASE} is set, so accounts need {', '.join(missing)} too")
