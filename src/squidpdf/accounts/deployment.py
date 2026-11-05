"""Accounts as the deployment configures them, from its environment (env.example).

None of it is set on a self-hosted copy, which then keeps no accounts. Naming the database
but not the rest, or an allowance with no database or only half of it, stops the app
starting, saying what's missing, rather than failing on the first sign-in or upload.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from squidpdf.accounts.mail import MailController, Security, SmtpMailer
from squidpdf.accounts.users import UserStore, open_users

# The SQLite file accounts are kept in; unset or empty, the server keeps none.
_DATABASE = "SQUIDPDF_DATABASE"
# Signs every link and sign-in: generated, never chosen (env.example says how).
_SECRET = "SQUIDPDF_SECRET"
_SITE = "SQUIDPDF_SITE"  # the https:// address a link opens, as https://pdf.example.com
# Set to anything, only those `squidpdf invite` let in sign in and open documents.
_INVITE_ONLY = "SQUIDPDF_INVITE_ONLY"
_SMTP = (
    "SQUIDPDF_SMTP_HOST",
    "SQUIDPDF_SMTP_PORT",
    "SQUIDPDF_SMTP_SECURITY",
    "SQUIDPDF_SMTP_USER",
    "SQUIDPDF_SMTP_PASSWORD",
    "SQUIDPDF_MAIL_FROM",
)
# Each way the environment may name how the connection is secured.
_SECURITIES: dict[str, Security] = {"tls": "tls", "starttls": "starttls"}
# How many documents a browser opens without an account, and an account opens a month. No
# default: the numbers are the business's, so each lives in the deployment's .env alone.
_FREE_DOCUMENTS = "SQUIDPDF_FREE_DOCUMENTS"
_DOCUMENTS_A_MONTH = "SQUIDPDF_DOCUMENTS_A_MONTH"
_ALLOWANCE = (_FREE_DOCUMENTS, _DOCUMENTS_A_MONTH)
_MIN_SECRET_CHARS = 32  # 192 bits as base64, the least; env.example's command writes 256


class AccountsUnset(RuntimeError):
    """Accounts or metering half set up, or set up with something unusable."""


@dataclass(frozen=True, slots=True)
class Allowance:
    """How many documents a browser opens: without an account, then with one, a month."""

    free: int  # documents a browser opens without an account
    monthly: int


@dataclass(frozen=True, slots=True, eq=False)
class Accounts:
    """What accounts need: the table, the mail, the secret and address, who may, how many."""

    users: UserStore
    mail: MailController
    secret: bytes  # signs links and the signed-in cookie
    site: str  # where a link sends its reader, with no slash at the end
    invite_only: bool  # only those `squidpdf invite` let in sign in and open documents
    allowance: Allowance | None  # None where the deployment meters nothing


def named_users() -> UserStore | None:
    """The users table in the file `SQUIDPDF_DATABASE` names; None where it names none."""
    named = os.environ.get(_DATABASE)  # .get: a self-hosted copy names none
    if not named:
        return None
    return open_users(Path(named))


def deployed_accounts() -> Accounts | None:
    """Accounts as the environment sets them up; None where it names no database."""
    users = named_users()
    if users is None:
        _refuse_metering_without_accounts()
        return None
    _require(_SECRET, _SITE, *_SMTP)
    secret = os.environ[_SECRET]
    if len(secret) < _MIN_SECRET_CHARS:
        raise AccountsUnset(f"{_SECRET} is shorter than {_MIN_SECRET_CHARS} characters")
    site = os.environ[_SITE].rstrip("/")
    if not site.startswith("https://"):
        raise AccountsUnset(f"{_SITE} must start with https://, as a link it opens must")
    return Accounts(
        users,
        MailController(_mailer()),
        secret=secret.encode(),
        site=site,
        invite_only=bool(os.environ.get(_INVITE_ONLY)),  # .get: unset, anyone may sign in
        allowance=_allowance(),
    )


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
    """Raise AccountsUnset if an allowance is set with no database to keep accounts in."""
    if any(os.environ.get(name) for name in _ALLOWANCE):  # .get: unset is the usual case
        raise AccountsUnset(f"metering asks for an account, so it needs {_DATABASE} too")


def _count_in(name: str) -> int:
    """The number of documents the variable `name` sets, or AccountsUnset if it isn't one."""
    value = os.environ[name]
    if not value.isdigit():
        raise AccountsUnset(f"{name} is {value!r}, not a number of documents")
    return int(value)


def _mailer() -> SmtpMailer:
    """The mail server the environment names."""
    host, port, security, user, password, sender = (os.environ[name] for name in _SMTP)
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
