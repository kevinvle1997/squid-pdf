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
    Select,
    Table,
    Text,
    case,
    create_engine,
    event,
    select,
    text,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from squidpdf.accounts import constants
from squidpdf.accounts.errors import NotAnEmail

_ID_BYTES = 16
# What a To: header reads as one address ending and another, or a name, beginning.
_SEPARATORS = frozenset(',;<>()[]"\\:')

_TABLES = MetaData()
_USERS = Table(
    "users",
    _TABLES,
    Column("id", Text, primary_key=True),
    Column("email", Text, unique=True, nullable=False),  # as `checked_email` writes it
    Column("created_at", Integer, nullable=False),  # Unix seconds
    # Uses as many documents as they like: friends and family, given it with `squidpdf invite`.
    Column("is_unlimited", Boolean, nullable=False, default=False),
    # Metering's: the month `counted` is for, as "2026-11", and each file counted in it, by its
    # digest, a space apart (`metering.py`).
    Column("month", Text, nullable=True),
    Column("counted", Text, nullable=False, default=""),
    # When mail to the address last came back as never deliverable; None if it never has.
    Column("bounced_at", Integer, nullable=True),
    # Until when the last sign-in link used held (Unix seconds); None until one is used. A link
    # is used once, and an older one is used up with it.
    Column("link_until", Integer, nullable=True),
)
# Columns added since the table was first made, each added to a table from before it.
_ADDED_COLUMNS = ("bounced_at", "link_until")


@dataclass(frozen=True, slots=True)
class User:
    """Someone with an account."""

    id: str  # random, never their email, so a cookie that names it says nothing of them
    email: str
    created_at: int
    is_unlimited: bool
    bounced_at: int | None  # mail to them came back as never deliverable, so none is sent


@dataclass(frozen=True, slots=True, eq=False)
class UserStore:
    """The users table: who's invited, who has signed in, and who has no document limit.

    An account taken off with `remove` is gone from every browser signed in to it: each asks
    the table, so its signed-in cookie names no one.
    """

    database: Engine

    def find(self, email: str) -> User | None:
        """The account under `email`; None if there's none."""
        return self._one(select(_USERS).where(_USERS.c.email == email))

    def get(self, user_id: str) -> User | None:
        """The account with `user_id`; None if there's none."""
        return self._one(select(_USERS).where(_USERS.c.id == user_id))

    def joined(self, email: str, *, now: int) -> User:
        """The account under `email`, made now if it has none: the first sign-in makes it."""
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=False))
        with self.database.begin() as connection:
            connection.execute(added.on_conflict_do_nothing(index_elements=[_USERS.c.email]))
            return _user_written(connection, email)

    def invite(self, email: str, *, now: int, is_unlimited: bool) -> User:
        """The account under `email`, made if need be, with or without a document limit."""
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=is_unlimited))
        kept = added.on_conflict_do_update(
            index_elements=[_USERS.c.email],
            # Invited again, they're trusted to have mended their address.
            set_={"is_unlimited": is_unlimited, "bounced_at": None},
        )
        with self.database.begin() as connection:
            connection.execute(kept)
            return _user_written(connection, email)

    def note_bounce(self, email: str, *, now: int) -> bool:
        """Note that mail to `email` can never be delivered; whether an account has it."""
        noted = _USERS.update().where(_USERS.c.email == email).values(bounced_at=now)
        with self.database.begin() as connection:
            return connection.execute(noted).rowcount > 0

    def spend_link(self, email: str, *, until: int) -> bool:
        """Use up the link that holds `until` for `email`'s account; whether it was unused.

        One statement, so a link traded twice at once signs in once. An older link is used
        up with a newer one, so a link left in an inbox works once, and only the newest.
        """
        unused = _USERS.c.link_until.is_(None) | (_USERS.c.link_until < until)
        spending = (
            _USERS.update().where(_USERS.c.email == email, unused).values(link_until=until)
        )
        with self.database.begin() as connection:
            return connection.execute(spending).rowcount > 0

    def remove(self, email: str) -> bool:
        """Take the account under `email` off the table, its count too; whether it had one."""
        with self.database.begin() as connection:
            removed = connection.execute(_USERS.delete().where(_USERS.c.email == email))
        return removed.rowcount > 0

    def counted_in(self, user_id: str, *, month: str) -> frozenset[str]:
        """The files the account opened in `month`, by digest: none, once a new one starts.

        Nothing, too, for an account removed while its request was under way.
        """
        query = select(_USERS.c.month, _USERS.c.counted).where(_USERS.c.id == user_id)
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            return frozenset()
        return _counted_from(row.month, row.counted, month=month)

    def count(self, user_id: str, digest: str, *, month: str) -> None:
        """Count the file `digest` against the account in `month`, a new month afresh.

        One statement, so two uploads counted at once both count: SQLite's driver starts
        a transaction only at the write, so a read before it could be overwritten.
        """
        this_month = _USERS.c.month == month
        listed = (" " + _USERS.c.counted + " ").contains(f" {digest} ", autoescape=True)
        added = case(
            (this_month & (_USERS.c.counted != ""), _USERS.c.counted + " " + digest),
            else_=digest,
        )
        # No row for an account removed while its upload was under way: nothing to add to.
        counting = (
            _USERS.update()
            .where(_USERS.c.id == user_id, ~(this_month & listed))
            .values(month=month, counted=added)
        )
        with self.database.begin() as connection:
            connection.execute(counting)

    def _one(self, query: Select[Any]) -> User | None:
        """The one account `query` finds; None if it finds none."""
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        return None if row is None else _user_from(row)


def open_users(path: Path) -> UserStore:
    """The users table in the SQLite file at `path`, made with its folder if new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A connection per use, closed after: a file opens in microseconds, and none is left open.
    database = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    event.listen(database, "connect", _set_up_connection)
    _TABLES.create_all(database)
    for column in _ADDED_COLUMNS:
        _add_column(database, column)
    return UserStore(database)


def checked_email(typed: str) -> str:
    """`typed` as the table keeps it, trimmed, lower case; NotAnEmail if no mail reaches it."""
    email = typed.strip().lower()
    local, at, domain = email.rpartition("@")
    # A space, or what a mail header reads as a second address or a name, sends to others too.
    has_separator = any(character.isspace() or character in _SEPARATORS for character in email)
    looks_whole = bool(local and at) and "." in domain and not has_separator
    if not looks_whole or len(email) > constants.MAX_EMAIL_CHARS:
        raise NotAnEmail(debug=f"{len(email)} characters")
    return email


def _set_up_connection(connection: Any, _record: Any) -> None:
    """Each connection logs ahead, and overwrites what it deletes.

    Logging ahead, a reader never waits on a writer. Overwritten, an account removed
    leaves no address in the file's free space.
    """
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA secure_delete=ON")


def _add_column(database: Engine, column: str) -> None:
    """Add the nullable integer `column` to a table made before it was one; its rows read None.

    Tried on every open: SQLite has no ADD COLUMN IF NOT EXISTS, and a look first
    would race the app and the command line opening the file at once.
    """
    try:
        with database.begin() as connection:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {column} INTEGER"))
    except OperationalError as refused:  # raised by SQLite, as a column already there is
        if "duplicate column name" not in str(refused):
            raise


def _user_written(connection: Any, email: str) -> User:
    """The account under `email`, read in the transaction that wrote it: no removal between."""
    return _user_from(connection.execute(select(_USERS).where(_USERS.c.email == email)).one())


def _new_row(email: str, *, now: int, is_unlimited: bool) -> dict[str, Any]:
    """A new account's row."""
    return {
        "id": secrets.token_urlsafe(_ID_BYTES),
        "email": email,
        "created_at": now,
        "is_unlimited": is_unlimited,
    }


def _counted_from(counted_month: str | None, counted: str, *, month: str) -> frozenset[str]:
    """What a row counted in `month`; nothing if it last counted in another, or never."""
    if counted_month != month:
        return frozenset()
    return frozenset(counted.split())


def _user_from(row: Row[Any]) -> User:
    """A row of the table, read by name."""
    return User(
        row.id,
        row.email,
        created_at=row.created_at,
        is_unlimited=row.is_unlimited,
        bounced_at=row.bounced_at,
    )
