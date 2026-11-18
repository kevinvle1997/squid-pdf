"""The accounts' tables: who's signed in or invited, what billing did, and bounced addresses.

An address that bounced with no account is kept only as a digest keyed with the secret.
SQLite in one file, through SQLAlchemy, so moving to a server database later changes the
URL and little else. Every call blocks on the disk: a coroutine makes it in a thread.
"""

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
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from squidpdf.accounts import constants
from squidpdf.accounts.errors import MonthlyDocumentsUsed, NotAnEmail
from squidpdf.accounts.signed import digest_of
from squidpdf.accounts.types import BillingEvent, BillingOutcome

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
    # The account's customer at Stripe, made the first time it goes to pay; None before.
    Column("stripe_customer", Text, nullable=True),
    # Until when Stripe last said the account's month is paid (Unix seconds); None if never.
    Column("paid_until", Integer, nullable=True),
    # When Stripe was asked for that (Unix ms): an answer asked for before it never replaces it.
    Column("paid_read_at", Integer, nullable=True),
)
# Columns added since the table was first made, each added to a table from before it, with
# its SQL type: SQLite adds no column as UNIQUE, so the customer's index is made apart.
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
# Addresses mail can never reach that no account has, kept only as `_bounce_digest` makes
# them: keyed with the server's secret, so a copy of this file alone holds no stranger's.
_BOUNCED_ADDRESSES = Table(
    "bounced_addresses",
    _TABLES,
    Column("digest", Text, primary_key=True),
    Column("bounced_at", Integer, nullable=False),  # Unix seconds
)
# Every event Stripe has sent, once each, and what it did: never changed, never deleted, so it
# stays the record of what billing did, an account's removal included.
_BILLING_EVENTS = Table(
    "billing_events",
    _TABLES,
    Column("id", Text, primary_key=True),  # Stripe's: the same event sent again is no new row
    Column("type", Text, nullable=False),
    Column("created", Integer, nullable=False),  # Stripe's time, Unix seconds
    Column("received_at", Integer, nullable=False),  # ours, Unix seconds
    Column("customer", Text, nullable=True),
    Column("account", Text, nullable=True),  # an account's id, kept once the account is gone
    Column("outcome", Text, nullable=False),
    Column("paid_until", Integer, nullable=True),
    Column("paid_read_at", Integer, nullable=True),  # when Stripe was asked, Unix ms
)

# What `remove` did: took the account off, found none, or kept it, as it's paying.
Removal = Literal["removed", "no_account", "paying"]


@dataclass(frozen=True, slots=True)
class User:
    """Someone with an account."""

    id: str  # random, never their email, so a cookie that names it says nothing of them
    email: str
    created_at: int
    is_unlimited: bool
    bounced_at: int | None  # mail to them came back as never deliverable, so none is sent
    stripe_customer: str | None  # their customer at Stripe; None until they first go to pay
    paid_until: int | None  # until when Stripe last said their month is paid; None if never

    def is_paid_at(self, now: int) -> bool:
        """Whether paid time runs at `now`, a renewal's charge allowed for: no limit then."""
        return (
            self.paid_until is not None and now < self.paid_until + constants.RENEWAL_LEEWAY_S
        )

    def has_no_limit_at(self, now: int) -> bool:
        """Whether no document limit applies at `now`: unlimited, or its month paid."""
        return self.is_unlimited or self.is_paid_at(now)


@dataclass(frozen=True, slots=True, eq=False)
class UserStore:
    """The accounts, each with its document limit and Stripe customer, and billing's ledger.

    The ledger keeps every event Stripe sent, and what it did. An account taken off with
    `remove` is gone from every browser signed in to it: each asks the table, so its
    signed-in cookie names no one.
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

    def invite(self, email: str, *, now: int, is_unlimited: bool, secret: bytes) -> User:
        """The account under `email`, made if need be, with or without a document limit.

        Invited, the address is trusted mended: a bounce kept for it, by `secret`, goes.
        """
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
        """Note that mail to `email` can never be delivered; whether an account has it.

        An address no account has is kept only as a digest keyed with `secret`.
        """
        noted = _USERS.update().where(_USERS.c.email == email).values(bounced_at=now)
        digest = _bounce_digest(email, secret=secret)
        kept = (
            insert(_BOUNCED_ADDRESSES)
            .values(digest=digest, bounced_at=now)
            .on_conflict_do_update(
                index_elements=[_BOUNCED_ADDRESSES.c.digest], set_={"bounced_at": now}
            )
        )
        with self.database.begin() as connection:
            if connection.execute(noted).rowcount > 0:
                return True
            connection.execute(kept)
            return False

    def has_bounced(self, email: str, *, secret: bytes) -> bool:
        """Whether mail to `email` came back as never deliverable, to an account or not."""
        account = self.find(email)
        # An account is the record once there is one, so its invitation clears it even where
        # the command line's secret wasn't the server's.
        if account is not None:
            return account.bounced_at is not None
        digest = _BOUNCED_ADDRESSES.c.digest == _bounce_digest(email, secret=secret)
        with self.database.connect() as connection:
            return (
                connection.execute(select(_BOUNCED_ADDRESSES).where(digest)).first() is not None
            )

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

    def remove(self, email: str, *, now: int) -> Removal:
        """Take the account under `email` off the table, its count too, unless it's paying.

        One transaction, so paid time a webhook sets can't land between the look and the
        delete: an account whose month is paid would be charged for nothing.
        """
        unpaid = _USERS.c.paid_until.is_(None) | (
            _USERS.c.paid_until <= now - constants.RENEWAL_LEEWAY_S
        )
        with self.database.begin() as connection:
            removed = connection.execute(_USERS.delete().where(_USERS.c.email == email, unpaid))
            if removed.rowcount > 0:
                return "removed"
            kept = connection.execute(select(_USERS.c.id).where(_USERS.c.email == email))
            return "no_account" if kept.first() is None else "paying"

    def find_by_customer(self, customer: str) -> User | None:
        """The account whose Stripe customer is `customer`; None if none has it."""
        return self._one(select(_USERS).where(_USERS.c.stripe_customer == customer))

    def keep_customer(self, user_id: str, customer: str) -> str | None:
        """The account's Stripe customer: `customer` if it had none; None if it's gone.

        One transaction, so an account asked to pay twice at once keeps one customer.
        """
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
        """Forget the account's Stripe customer, if still `customer`: Stripe hasn't got it."""
        forgotten = (
            _USERS.update()
            .where(_USERS.c.id == user_id, _USERS.c.stripe_customer == customer)
            .values(stripe_customer=None)
        )
        with self.database.begin() as connection:
            connection.execute(forgotten)

    def has_ledger_event(self, event_id: str) -> bool:
        """Whether the ledger has the event `event_id` already."""
        query = select(_BILLING_EVENTS.c.id).where(_BILLING_EVENTS.c.id == event_id)
        with self.database.connect() as connection:
            return connection.execute(query).first() is not None

    def ledger_of(self, user_id: str) -> list[BillingEvent]:
        """Every event the ledger has that set the account's paid time, oldest first."""
        query = (
            select(_BILLING_EVENTS)
            .where(_BILLING_EVENTS.c.account == user_id)
            .order_by(_BILLING_EVENTS.c.created, _BILLING_EVENTS.c.received_at)
        )
        with self.database.connect() as connection:
            rows = connection.execute(query).all()
        return [_billing_event_from(row) for row in rows]

    def apply_billing(self, event: BillingEvent, *, now: int) -> BillingOutcome | None:
        """Set the paid time `event` says, and keep it in the ledger with what it did.

        One transaction, so the ledger says what each event did. Paid time Stripe was asked
        for before the time kept already is kept in the ledger only: the later read stands,
        whichever process, or event, made it. None for an event the ledger has already.
        """
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
        """The files the account opened in `month`, by digest: none, once a new one starts.

        Nothing, too, for an account removed while its request was under way.
        """
        query = select(_USERS.c.month, _USERS.c.counted).where(_USERS.c.id == user_id)
        with self.database.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            return frozenset()
        return _counted_from(row.month, row.counted, month=month)

    def count(self, user_id: str, digest: str, *, month: str, limit: int | None) -> None:
        """Count the file `digest` against the account in `month`, a new month afresh.

        Raises MonthlyDocumentsUsed if `digest` is new and `limit` are counted; None is
        no limit. One statement, so two uploads at once can't both pass the limit, or
        overwrite each other: SQLite's driver starts a transaction only at the write.
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
            .where(_USERS.c.id == user_id, ~(this_month & listed), _has_room(month, limit))
            .values(month=month, counted=added)
        )
        query = select(_USERS.c.month, _USERS.c.counted).where(_USERS.c.id == user_id)
        with self.database.begin() as connection:
            if connection.execute(counting).rowcount > 0:
                return
            # Read in the write's transaction, so no other count lands between.
            row = connection.execute(query).first()
        # With no limit, or no account, nothing is refused: the file was listed already.
        if limit is None or row is None:
            return
        if digest not in _counted_from(row.month, row.counted, month=month):
            raise MonthlyDocumentsUsed(monthly=limit)

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
    # IF NOT EXISTS: create_all looks, then makes, which races the app and the command line
    # opening a new file at once.
    with database.begin() as connection:
        for table in _TABLES.sorted_tables:
            connection.execute(CreateTable(table, if_not_exists=True))
    for column, kind in _ADDED_COLUMNS.items():
        _add_column(database, column, kind=kind)
    with database.begin() as connection:
        connection.execute(text(_ONE_ACCOUNT_A_CUSTOMER))
    return UserStore(database)


def this_month() -> str:
    """The month a count is for, as "2026-11", in UTC: the same for every server."""
    return datetime.now(UTC).strftime("%Y-%m")


def _bounce_digest(email: str, *, secret: bytes) -> str:
    """`email` as the bounced addresses keep one no account has: keyed with `secret`."""
    return digest_of(email, purpose="bounced", secret=secret)


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


def _add_column(database: Engine, column: str, *, kind: str) -> None:
    """Add the nullable `column`, of SQL type `kind`, to a table made before it; rows read None.

    Tried on every open: SQLite has no ADD COLUMN IF NOT EXISTS, and a look first
    would race the app and the command line opening the file at once.
    """
    try:
        with database.begin() as connection:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {column} {kind}"))
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


def _has_room(month: str, limit: int | None) -> ColumnElement[bool]:
    """Whether a row counts fewer than `limit` files in `month`; always, with no limit."""
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
    """Set the paid time `event` says, unless the time kept was asked of Stripe later.

    An event the ledger has already sets none: a copy Stripe sent again may read it anew.
    """
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
    """A row of the ledger, read by name."""
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
    """A row of the table, read by name."""
    return User(
        row.id,
        row.email,
        created_at=row.created_at,
        is_unlimited=row.is_unlimited,
        bounced_at=row.bounced_at,
        stripe_customer=row.stripe_customer,
        paid_until=row.paid_until,
    )
