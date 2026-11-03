"""The one table: each person who has signed in or been invited, by their email.

SQLite in one file, through SQLAlchemy, so moving to a server database later changes the
URL and little else. Every call blocks on the disk: a coroutine makes it in a thread.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Integer,
    MetaData,
    Row,
    Table,
    Text,
    create_engine,
    event,
    select,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.pool import NullPool

from squidpdf.accounts import constants
from squidpdf.accounts.errors import NotAnEmail

_ID_BYTES = 16

_TABLES = MetaData()
_USERS = Table(
    "users",
    _TABLES,
    Column("id", Text, primary_key=True),
    Column("email", Text, unique=True, nullable=False),  # as `checked_email` writes it
    Column("created_at", Integer, nullable=False),  # Unix seconds
    # Uses as many documents as they like: friends and family, given it with `squidpdf invite`.
    Column("is_unlimited", Boolean, nullable=False, default=False),
    # Read once metering lands, stacked on this: the month `counted` is for, as "2026-11", and
    # each file counted in it, by its digest. Here now, so a table made before needs no change.
    Column("period", Text, nullable=True),
    Column("counted", Text, nullable=False, default=""),
)


@dataclass(frozen=True, slots=True)
class User:
    """Someone with an account."""

    id: str  # random, never their email, so a cookie that names it says nothing of them
    email: str
    created_at: int
    is_unlimited: bool


@dataclass(frozen=True, slots=True, eq=False)
class UserStore:
    """The users table: who's invited, who has signed in, and who has no document limit."""

    database: Engine

    def find(self, email: str) -> User | None:
        """The account under `email`; None if there's none."""
        return self._one(select(_USERS).where(_USERS.c.email == email))

    def get(self, user_id: str) -> User | None:
        """The account with `user_id`; None if there's none, as after it's deleted."""
        return self._one(select(_USERS).where(_USERS.c.id == user_id))

    def joined(self, email: str, *, now: int) -> User:
        """The account under `email`, made now if it has none: the first sign-in makes it."""
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=False))
        with self.database.begin() as connection:
            connection.execute(added.on_conflict_do_nothing(index_elements=[_USERS.c.email]))
        return self._required(email)

    def invite(self, email: str, *, now: int, is_unlimited: bool) -> User:
        """The account under `email`, made if need be, with or without a document limit."""
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=is_unlimited))
        kept = added.on_conflict_do_update(
            index_elements=[_USERS.c.email], set_={"is_unlimited": is_unlimited}
        )
        with self.database.begin() as connection:
            connection.execute(kept)
        return self._required(email)

    def _required(self, email: str) -> User:
        """The account under `email`, which was just written."""
        user = self.find(email)
        if user is None:
            raise RuntimeError(f"no account just after writing one for {email!r}")
        return user

    def _one(self, query: Any) -> User | None:
        """The one account `query` finds; None if it finds none."""
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        return None if row is None else _user_from(row)


def open_users(path: Path) -> UserStore:
    """The users table in the SQLite file at `path`, made with its folder if new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A connection per use, closed after: a file opens in microseconds, and none is left open.
    database = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    event.listen(database, "connect", _write_ahead)
    _TABLES.create_all(database)
    return UserStore(database)


def checked_email(typed: str) -> str:
    """`typed` as the table keeps it, trimmed, lower case; NotAnEmail if no mail reaches it."""
    email = typed.strip().lower()
    local, at, domain = email.rpartition("@")
    has_space = any(character.isspace() for character in email)
    looks_whole = bool(local and at) and "." in domain and not has_space
    if not looks_whole or len(email) > constants.MAX_EMAIL_CHARS:
        raise NotAnEmail(debug=f"{len(email)} characters")
    return email


def _write_ahead(connection: Any, _record: Any) -> None:
    """Each connection logs ahead, so a reader never waits on a writer."""
    connection.execute("PRAGMA journal_mode=WAL")


def _new_row(email: str, *, now: int, is_unlimited: bool) -> dict[str, Any]:
    """A new account's row."""
    return {
        "id": secrets.token_urlsafe(_ID_BYTES),
        "email": email,
        "created_at": now,
        "is_unlimited": is_unlimited,
    }


def _user_from(row: Row[Any]) -> User:
    """A row of the table, read by name."""
    return User(row.id, row.email, created_at=row.created_at, is_unlimited=row.is_unlimited)
