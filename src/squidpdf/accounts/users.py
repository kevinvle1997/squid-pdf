"""Every call blocks on the disk: a coroutine makes it in a thread."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    Column,
    ColumnElement,
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
    exists,
    func,
    select,
    text,
    true,
)
from sqlalchemy.dialects.sqlite import Insert, insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from squidpdf.accounts import constants
from squidpdf.accounts.errors import MonthlyDocumentsUsed, NotAnEmail
from squidpdf.accounts.signed import digest_of
from squidpdf.accounts.types import BillingEvent, BillingOutcome

_ID_BYTES = 16
_SEPARATORS = frozenset(',;<>()[]"\\:')

_TABLES = MetaData()
_USERS = Table(
    "users",
    _TABLES,
    Column("id", Text, primary_key=True),
    Column("email", Text, unique=True, nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("is_unlimited", Boolean, nullable=False, default=False),
    Column("month", Text, nullable=True),
    Column("counted", Text, nullable=False, default=""),
    Column("bounced_at", Integer, nullable=True),
    Column("link_until", Integer, nullable=True),
    Column("stripe_customer", Text, nullable=True),
    Column("paid_until", Integer, nullable=True),
    # When Stripe was asked (Unix ms): an answer asked for earlier never replaces it.
    Column("paid_read_at", Integer, nullable=True),
)
# SQLite adds no column as UNIQUE, so the customer's index is made apart.
_ADDED_COLUMNS = {
    "bounced_at": "INTEGER",
    "link_until": "INTEGER",
    "stripe_customer": "TEXT",
    "paid_until": "INTEGER",
    "paid_read_at": "INTEGER",
}
_ONE_ACCOUNT_A_CUSTOMER = (
    "CREATE UNIQUE INDEX IF NOT EXISTS users_stripe_customer ON users (stripe_customer)"
)
# Keyed with the server's secret, so a copy of this file alone holds no stranger's address.
_BOUNCED_ADDRESSES = Table(
    "bounced_addresses",
    _TABLES,
    Column("digest", Text, primary_key=True),
    Column("bounced_at", Integer, nullable=False),
)
# Never changed, never deleted: the record of what billing did, removals included.
_BILLING_EVENTS = Table(
    "billing_events",
    _TABLES,
    Column("id", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("created", Integer, nullable=False),
    Column("received_at", Integer, nullable=False),
    Column("customer", Text, nullable=True),
    Column("account", Text, nullable=True),
    Column("outcome", Text, nullable=False),
    Column("paid_until", Integer, nullable=True),
    Column("paid_read_at", Integer, nullable=True),
)

Removal = Literal["removed", "no_account", "paying"]


@dataclass(frozen=True, slots=True)
class User:
    id: str  # random, never their email, so a cookie that names it says nothing of them
    email: str
    created_at: int
    is_unlimited: bool
    bounced_at: int | None
    stripe_customer: str | None
    paid_until: int | None

    def is_paid_at(self, now: int) -> bool:
        return (
            self.paid_until is not None and now < self.paid_until + constants.RENEWAL_LEEWAY_S
        )

    def has_no_limit_at(self, now: int) -> bool:
        return self.is_unlimited or self.is_paid_at(now)


@dataclass(frozen=True, slots=True, eq=False)
class UserStore:
    database: Engine

    def find(self, email: str) -> User | None:
        return self._one(select(_USERS).where(_USERS.c.email == email))

    def get(self, user_id: str) -> User | None:
        return self._one(select(_USERS).where(_USERS.c.id == user_id))

    def joined(self, email: str, *, now: int) -> User:
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=False))
        with self.database.begin() as connection:
            connection.execute(added.on_conflict_do_nothing(index_elements=[_USERS.c.email]))
            return _user_written(connection, email)

    def invite(self, email: str, *, now: int, is_unlimited: bool, secret: bytes) -> User:
        added = insert(_USERS).values(_new_row(email, now=now, is_unlimited=is_unlimited))
        kept = added.on_conflict_do_update(
            index_elements=[_USERS.c.email],
            set_={"is_unlimited": is_unlimited, "bounced_at": None},
        )
        digest = _BOUNCED_ADDRESSES.c.digest == _bounce_digest(email, secret=secret)
        with self.database.begin() as connection:
            connection.execute(kept)
            connection.execute(_BOUNCED_ADDRESSES.delete().where(digest))
            return _user_written(connection, email)

    def note_bounce(self, email: str, *, secret: bytes, now: int) -> bool:
        noted = _USERS.update().where(_USERS.c.email == email).values(bounced_at=now)
        with self.database.begin() as connection:
            if connection.execute(noted).rowcount > 0:
                return True
            connection.execute(_address_bounced(email, secret=secret, at=now))
            return False

    def has_bounced(self, email: str, *, secret: bytes) -> bool:
        account = self.find(email)
        if account is not None:
            return account.bounced_at is not None
        digest = _BOUNCED_ADDRESSES.c.digest == _bounce_digest(email, secret=secret)
        with self.database.connect() as connection:
            return (
                connection.execute(select(_BOUNCED_ADDRESSES).where(digest)).first() is not None
            )

    def spend_link(self, email: str, *, until: int) -> bool:
        """One statement, so a link traded twice at once signs in once."""
        unused = _USERS.c.link_until.is_(None) | (_USERS.c.link_until < until)
        spending = (
            _USERS.update().where(_USERS.c.email == email, unused).values(link_until=until)
        )
        with self.database.begin() as connection:
            return connection.execute(spending).rowcount > 0

    def remove(self, email: str, *, now: int, secret: bytes) -> Removal:
        """One transaction, so paid time a webhook sets can't land between look and delete."""
        unpaid = _USERS.c.paid_until.is_(None) | (
            _USERS.c.paid_until <= now - constants.RENEWAL_LEEWAY_S
        )
        deleted = (
            _USERS.delete()
            .where(_USERS.c.email == email, unpaid)
            .returning(_USERS.c.bounced_at)
        )
        with self.database.begin() as connection:
            removed = connection.execute(deleted).first()
            if removed is not None and removed.bounced_at is not None:
                connection.execute(
                    _address_bounced(email, secret=secret, at=removed.bounced_at)
                )
            if removed is not None:
                return "removed"
            kept = connection.execute(select(_USERS.c.id).where(_USERS.c.email == email))
            return "no_account" if kept.first() is None else "paying"

    def find_by_customer(self, customer: str) -> User | None:
        return self._one(select(_USERS).where(_USERS.c.stripe_customer == customer))

    def keep_customer(self, user_id: str, customer: str) -> str | None:
        """One transaction, so an account asked to pay twice at once keeps one customer."""
        kept = (
            _USERS.update()
            .where(_USERS.c.id == user_id, _USERS.c.stripe_customer.is_(None))
            .values(stripe_customer=customer)
        )
        with self.database.begin() as connection:
            connection.execute(kept)
            query = select(_USERS.c.stripe_customer).where(_USERS.c.id == user_id)
            row = connection.execute(query).first()
        return None if row is None else row.stripe_customer

    def forget_customer(self, user_id: str, customer: str) -> None:
        forgotten = (
            _USERS.update()
            .where(_USERS.c.id == user_id, _USERS.c.stripe_customer == customer)
            .values(stripe_customer=None)
        )
        with self.database.begin() as connection:
            connection.execute(forgotten)

    def has_ledger_event(self, event_id: str) -> bool:
        query = select(_BILLING_EVENTS.c.id).where(_BILLING_EVENTS.c.id == event_id)
        with self.database.connect() as connection:
            return connection.execute(query).first() is not None

    def ledger_of(self, user_id: str) -> list[BillingEvent]:
        query = (
            select(_BILLING_EVENTS)
            .where(_BILLING_EVENTS.c.account == user_id)
            .order_by(_BILLING_EVENTS.c.created, _BILLING_EVENTS.c.received_at)
        )
        with self.database.connect() as connection:
            rows = connection.execute(query).all()
        return [_billing_event_from(row) for row in rows]

    def apply_billing(self, event: BillingEvent, *, now: int) -> BillingOutcome | None:
        """One transaction: paid time asked of Stripe before the time kept is ledger only."""
        outcome = event.outcome
        with self.database.begin() as connection:
            if outcome == "applied" and not _paid_time_set(connection, event):
                outcome = "superseded"
            kept = insert(_BILLING_EVENTS).values(
                id=event.id,
                type=event.type,
                created=event.created,
                received_at=now,
                customer=event.customer,
                account=event.account,
                outcome=outcome,
                paid_until=event.paid_until,
                paid_read_at=event.paid_read_at,
            )
            noted = connection.execute(kept.on_conflict_do_nothing(index_elements=["id"]))
            return outcome if noted.rowcount > 0 else None

    def counted_in(self, user_id: str, *, month: str) -> frozenset[str]:
        query = select(_USERS.c.month, _USERS.c.counted).where(_USERS.c.id == user_id)
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            return frozenset()
        return _counted_from(row.month, row.counted, month=month)

    def count(self, user_id: str, digest: str, *, month: str, limit: int | None) -> None:
        """One statement, so two uploads at once can't both pass the limit.

        SQLite's driver starts a transaction only at the write.
        """
        this_month = _USERS.c.month == month
        listed = (" " + _USERS.c.counted + " ").contains(f" {digest} ", autoescape=True)
        added = case(
            (this_month & (_USERS.c.counted != ""), _USERS.c.counted + " " + digest),
            else_=digest,
        )
        counting = (
            _USERS.update()
            .where(_USERS.c.id == user_id, ~(this_month & listed), _has_room(month, limit))
            .values(month=month, counted=added)
        )
        query = select(_USERS.c.month, _USERS.c.counted).where(_USERS.c.id == user_id)
        with self.database.begin() as connection:
            if connection.execute(counting).rowcount > 0:
                return
            # Read in the write's transaction, so no other count lands between.
            row = connection.execute(query).first()
        if limit is None or row is None:
            return
        if digest not in _counted_from(row.month, row.counted, month=month):
            raise MonthlyDocumentsUsed(monthly=limit)

    def _one(self, query: Select[Any]) -> User | None:
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        return None if row is None else _user_from(row)


def open_users(path: Path) -> UserStore:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    event.listen(database, "connect", _set_up_connection)
    # IF NOT EXISTS: create_all looks, then makes, which races the app and the command line.
    with database.begin() as connection:
        for table in _TABLES.sorted_tables:
            connection.execute(CreateTable(table, if_not_exists=True))
    for column, kind in _ADDED_COLUMNS.items():
        _add_column(database, column, kind=kind)
    with database.begin() as connection:
        connection.execute(text(_ONE_ACCOUNT_A_CUSTOMER))
    return UserStore(database)


def this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _address_bounced(email: str, *, secret: bytes, at: int) -> Insert:
    digest = _bounce_digest(email, secret=secret)
    return (
        insert(_BOUNCED_ADDRESSES)
        .values(digest=digest, bounced_at=at)
        .on_conflict_do_update(
            index_elements=[_BOUNCED_ADDRESSES.c.digest], set_={"bounced_at": at}
        )
    )


def _bounce_digest(email: str, *, secret: bytes) -> str:
    return digest_of(email, purpose="bounced", secret=secret)


def checked_email(typed: str) -> str:
    email = typed.strip().lower()
    local, at, domain = email.rpartition("@")
    # A space, or what a mail header reads as a second address or a name, sends to others too.
    has_separator = any(character.isspace() or character in _SEPARATORS for character in email)
    looks_whole = bool(local and at) and "." in domain and not has_separator
    if not looks_whole or len(email) > constants.MAX_EMAIL_CHARS:
        raise NotAnEmail(debug=f"{len(email)} characters")
    return email


def _set_up_connection(connection: Any, _record: Any) -> None:
    """Logging ahead, readers never wait on writers; overwritten, no removed address is left."""
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA secure_delete=ON")


def _add_column(database: Engine, column: str, *, kind: str) -> None:
    """Tried on every open: SQLite has no ADD COLUMN IF NOT EXISTS; a look first would race."""
    try:
        with database.begin() as connection:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {column} {kind}"))
    except OperationalError as refused:
        if "duplicate column name" not in str(refused):
            raise


def _user_written(connection: Any, email: str) -> User:
    return _user_from(connection.execute(select(_USERS).where(_USERS.c.email == email)).one())


def _new_row(email: str, *, now: int, is_unlimited: bool) -> dict[str, Any]:
    return {
        "id": secrets.token_urlsafe(_ID_BYTES),
        "email": email,
        "created_at": now,
        "is_unlimited": is_unlimited,
    }


def _counted_from(counted_month: str | None, counted: str, *, month: str) -> frozenset[str]:
    if counted_month != month:
        return frozenset()
    return frozenset(counted.split())


def _has_room(month: str, limit: int | None) -> ColumnElement[bool]:
    if limit is None:
        return true()
    spaces = func.length(_USERS.c.counted) - func.length(
        func.replace(_USERS.c.counted, " ", "")
    )
    in_month = case(
        ((_USERS.c.month == month) & (_USERS.c.counted != ""), spaces + 1),
        else_=0,
    )
    return in_month < limit


def _paid_time_set(connection: Any, event: BillingEvent) -> bool:
    asked_later = _USERS.c.paid_read_at.is_not(None) & (
        _USERS.c.paid_read_at >= event.paid_read_at
    )
    kept_already = exists().where(_BILLING_EVENTS.c.id == event.id)
    paid = (
        _USERS.update()
        .where(_USERS.c.id == event.account, ~asked_later, ~kept_already)
        .values(paid_until=event.paid_until, paid_read_at=event.paid_read_at)
    )
    return connection.execute(paid).rowcount > 0


def _billing_event_from(row: Row[Any]) -> BillingEvent:
    return BillingEvent(
        row.id,
        type=row.type,
        created=row.created,
        customer=row.customer,
        account=row.account,
        outcome=row.outcome,
        paid_until=row.paid_until,
        paid_read_at=row.paid_read_at,
    )


def _user_from(row: Row[Any]) -> User:
    return User(
        row.id,
        row.email,
        created_at=row.created_at,
        is_unlimited=row.is_unlimited,
        bounced_at=row.bounced_at,
        stripe_customer=row.stripe_customer,
        paid_until=row.paid_until,
    )
