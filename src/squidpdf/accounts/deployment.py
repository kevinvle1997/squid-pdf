"""Accounts as the deployment configures them, from its environment (env.example).

None of it is set on a self-hosted copy, which then keeps no accounts. Naming the database
but not the rest stops the app starting, saying what's missing, rather than failing on
the first sign-in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from squidpdf.accounts.mail import Mailer, Security, SmtpMailer
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
_MIN_SECRET_CHARS = 32  # 192 bits as base64, the least; env.example's command writes 256


class AccountsUnset(RuntimeError):
    """A database is named, but something accounts need isn't set, or isn't usable."""


@dataclass(frozen=True, slots=True, eq=False)
class Accounts:
    """What accounts need: the table, a way to send a link, the secret, the address, who may."""

    users: UserStore
    mailer: Mailer
    secret: bytes  # signs links and the signed-in cookie
    site: str  # where a link sends its reader, with no slash at the end
    invite_only: bool  # only those `squidpdf invite` let in sign in and open documents


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
        _mailer(),
        secret=secret.encode(),
        site=site,
        invite_only=bool(os.environ.get(_INVITE_ONLY)),  # .get: unset, anyone may sign in
    )


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
