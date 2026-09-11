"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3
import uuid
from datetime import datetime

from app.application.unit_of_work import UnitOfWork
from app.domain.money.currency import Currency
from app.domain.money.fundKind import FundKind
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    money_to_text,
    text_to_enum,
    text_to_money,
    uuid_to_text,
)
from app.infrastructure.repositories.sqlite_plan_notice_repository import (
    SqlitePlanNoticeRepository,
)
from app.infrastructure.repositories.sqlite_notification_repository import (
    SqliteNotificationRepository,
)
from app.infrastructure.repositories.sqlite_outbound_message_repository import (
    SqliteOutboundMessageRepository,
)
from app.infrastructure.repositories.sqlite_plan_run_repository import (
    SqlitePlanRunRepository,
)
from app.infrastructure.repositories.sqlite_savings_plan_repository import (
    SqliteSavingsPlanRepository,
)
from app.infrastructure.repositories.sqlite_transaction_repository import (
    SqliteTransactionRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    wallet_id         TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL,
    available_balance TEXT NOT NULL    -- decimal as text, e.g. "10000.00"
);

CREATE TABLE IF NOT EXISTS funds (
    fund_id       TEXT PRIMARY KEY,
    wallet_id     TEXT NOT NULL REFERENCES wallets(wallet_id),
    name          TEXT NOT NULL,    -- the handle a human types, e.g. "Vacation"
    kind          TEXT NOT NULL,    -- enum name: PERSONAL / BUSINESS
    balance       TEXT NOT NULL,    -- decimal as text, e.g. "50000.00"
    maturity_date TEXT,             -- ISO date; NULL means no maturity at all
    created_at    TEXT NOT NULL,    -- ISO moment; fixes the payout draw order
    -- There is deliberately no ``currency`` column. A fund is in its wallet's
    -- currency by construction - ``Wallet.__post_init__`` refuses one that is
    -- not - so a stored copy would be a second place for the same fact to live,
    -- free to disagree with the wallet it belongs to. See decision 33.
    --
    -- The UNIQUE is not tidiness either: a fund's name is the handle a human
    -- types (``fund release Vacation 5000``), so two funds called "Vacation" on
    -- one wallet would make that command ambiguous and the failure would be
    -- "some fund, chosen by the wrong rule" rather than "no such fund". The
    -- aggregate refuses a duplicate before it gets here; this is the backstop
    -- for the gap between the check and the write. See decision 37.
    UNIQUE (wallet_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     TEXT PRIMARY KEY,
    wallet_id          TEXT NOT NULL REFERENCES wallets(wallet_id),
    type               TEXT NOT NULL,
    amount             TEXT NOT NULL,
    currency           TEXT NOT NULL,
    internal_reference TEXT NOT NULL UNIQUE,   -- idempotency backstop
    provider_reference TEXT,
    narration          TEXT,
    metadata           TEXT,                   -- JSON
    destination        TEXT,                   -- JSON, payouts only
    fund_id            TEXT,                   -- which pot moved, when one did
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    reversed_at        TEXT
);

CREATE TABLE IF NOT EXISTS savings_plans (
    plan_id        TEXT PRIMARY KEY,
    wallet_id      TEXT NOT NULL REFERENCES wallets(wallet_id),
    name           TEXT NOT NULL,    -- what the user calls this plan, e.g. "Rent"
    source         TEXT NOT NULL,
    schedule       TEXT NOT NULL,    -- JSON: cadence name + anchor moment
    instructions   TEXT NOT NULL,    -- JSON array; value objects live with their root
    status         TEXT NOT NULL,
    completed_runs INTEGER NOT NULL, -- drift guard: next due = anchor + n, never anchor + 1
    ends_on        TEXT,             -- ISO date; NULL means open-ended
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_runs (
    plan_id     TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at      TEXT NOT NULL,   -- ISO moment of the occurrence this run is for
    status      TEXT NOT NULL,
    reason      TEXT,            -- enum name; blocked runs only, NULL otherwise
    recorded_at TEXT NOT NULL,
    -- The natural key, and the whole reason a retry is coherent: one plan has
    -- at most one run per occurrence, so re-saving rewrites rather than
    -- appends. A run cannot be identified by a generated id because it was
    -- never anything but (plan, occurrence).
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS plan_notices (
    plan_id   TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at    TEXT NOT NULL,   -- ISO moment of the occurrence being announced
    raised_at TEXT NOT NULL,   -- the tick's own notion of now when it was raised
    -- The same natural key as plan_runs, and for the same reason: a notice is
    -- nothing but (plan, occurrence), so it has no identity to generate. The
    -- primary key is what makes the warning once-only - a second tick inside
    -- the same window inserts nothing rather than warning the user again.
    --
    -- It is enforced here rather than by a "have we warned yet?" query for a
    -- reason worth keeping: a check followed by a write has a gap between them,
    -- and two overlapping ticks could both look, both find nothing, and both
    -- warn. The constraint closes the gap because the decision and the write
    -- are the same operation.
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS outbound_messages (
    plan_id     TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at      TEXT NOT NULL,   -- ISO moment of the occurrence being announced
    recipient   TEXT NOT NULL,   -- captured at enqueue, not resolved at send
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,   -- composed once; never re-rendered
    status      TEXT NOT NULL,   -- enum name: PENDING / SENT / EXPIRED
    attempts    INTEGER NOT NULL,
    last_error  TEXT,            -- the last delivery failure, NULL if none yet
    created_at  TEXT NOT NULL,
    settled_at  TEXT,            -- when it was sent or expired; NULL while owed
    -- The same natural key as plan_notices, and the same reason: a message is
    -- nothing but (plan, occurrence). The primary key is what makes a
    -- double-send unrepresentable - the notice claim already stops a second
    -- warning being raised, but a table whose rows become emails should not
    -- rely on another table's discipline to keep from mailing someone twice.
    --
    -- Deliberately a *separate* table from plan_notices despite sharing this
    -- key 1:1. A notice is inserted once and never touched - it has no truer
    -- second version (see SqlitePlanNoticeRepository.claim). A message is
    -- updated on every attempt, and its row carries a last_error. Keeping
    -- delivery state in its own row is what keeps "did the email go out?" out
    -- of reach of anything deciding whether a payout may proceed.
    PRIMARY KEY (plan_id, due_at)
);

CREATE TABLE IF NOT EXISTS notifications (
    event_key  TEXT NOT NULL,     -- derived from the event; see domain eventKey.py
    kind       TEXT NOT NULL,     -- enum name, e.g. PAYOUT_SUCCEEDED
    subject_id TEXT NOT NULL,     -- the plan or wallet the event is about
    recipient  TEXT NOT NULL,     -- captured at enqueue, not resolved at send
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,     -- composed once; never re-rendered
    status     TEXT NOT NULL,     -- enum name: PENDING / SENT / EXPIRED
    attempts   INTEGER NOT NULL,
    last_error TEXT,              -- the last delivery failure, NULL if none yet
    created_at TEXT NOT NULL,
    settled_at TEXT,              -- when it was sent or expired; NULL while owed
    -- Every message about something that *happened*, of every kind, in one
    -- table. Deliberately not keyed on (plan_id, due_at) as outbound_messages
    -- is: a deposit has no plan anywhere in it, and a payout receipt is not
    -- about an upcoming occurrence - the identity is the *event*, and an event
    -- is not always plan-shaped. The key is derived (see
    -- app/domain/notifications/eventKey.py), namespaced with its kind so a plan
    -- key and a wallet key cannot collide, and is the primary key so a duplicate
    -- send is unrepresentable rather than merely unlikely.
    --
    -- One table for every kind, rather than one per kind. The delivery rules -
    -- retry on failure, record the error, never raise, never re-send a settled
    -- row - are identical for all of them, so a table per kind would mean a
    -- drain per kind, each free to drift from the others in exactly the way that
    -- is hardest to notice.
    --
    -- There is deliberately no ``expires_at``. A warning stops being worth
    -- sending when its occurrence arrives; a receipt never does, because "your
    -- payout went out" does not become false. A nullable column that no code
    -- writes would advertise expiry as a live concept, so the column stays
    -- absent until a kind arrives that genuinely has a shelf life - and adding
    -- it then will be a migration, which is the price decision 18 named.
    --
    -- No migration function, for the reason the absence of one for plan_notices
    -- and outbound_messages already records: CREATE TABLE IF NOT EXISTS above
    -- creates a missing table on an existing database for free. Only a change to
    -- a table already on disk is a migration.
    PRIMARY KEY (event_key)
);
"""


def _migrate_legacy_transaction_types(connection: sqlite3.Connection) -> None:
    """Rewrite transaction types that were persisted under their old names.

    ``enum_to_text`` stores an enum member's *name*, so the type column holds
    "SCHEDULED_RELEASE" for rows written before that member was renamed to
    "UNLOCK_FUNDS". Reading such a row would now fail in ``text_to_enum`` with a
    bare KeyError.

    Renaming an enum member looks like a pure refactor but is really a data
    migration: the stored string is a contract, and breaking it breaks existing
    databases. Real projects run migrations through a tool (Alembic, Django
    migrations). This one is a standing ``UPDATE`` because it is idempotent -
    once no row matches, it is a no-op - which makes it safe to run on every
    connection.
    """
    connection.execute(
        """
        UPDATE transactions
        SET type = 'UNLOCK_FUNDS'
        WHERE type = 'SCHEDULED_RELEASE'
        """
    )


def _migrate_add_destination_column(connection: sqlite3.Connection) -> None:
    """Add transactions.destination to a database created before it existed.

    ``CREATE TABLE IF NOT EXISTS`` creates a *missing* table - it does nothing
    to a table that already exists, so a new column in SCHEMA reaches brand-new
    databases only. Every database already on disk needs an explicit ALTER.

    That is the general rule: **adding a field to a model is a schema migration,
    not an edit to the model.** The SCHEMA constant is the shape of a *new*
    database; migrations describe the path from an old one to it.

    Guarded by a check because SQLite has no ``ADD COLUMN IF NOT EXISTS`` - the
    guard is what makes this safe to re-run on every connection.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "destination" not in columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN destination TEXT")


def _migrate_add_plan_name_column(connection: sqlite3.Connection) -> None:
    """Add savings_plans.name to a database created before plans had names.

    Two things here are worth more than the code.

    First, the guard: SQLite has no ``ADD COLUMN IF NOT EXISTS``, so a check
    against ``PRAGMA table_info`` is what makes this safe to run on every
    connection. (Contrast ``_migrate_legacy_transaction_types``, where the
    idempotence comes from the ``WHERE`` clause instead - once no row matches,
    the statement does nothing.)

    Second, the DEFAULT. SQLite refuses to add a ``NOT NULL`` column to a table
    that already has rows unless a default is supplied - it has to put
    *something* in the existing rows, and ``NULL`` would violate the constraint.
    So the default is not a design choice about naming; it is the value every
    pre-existing plan gets, and it is written into the migration rather than
    into the SCHEMA for exactly that reason. A brand-new database never sees it.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(savings_plans)")
    }
    if "name" not in columns:
        connection.execute(
            "ALTER TABLE savings_plans "
            "ADD COLUMN name TEXT NOT NULL DEFAULT 'Untitled plan'"
        )


def _migrate_plan_run_due_at_to_datetime(connection: sqlite3.Connection) -> None:
    """Rewrite plan_runs.due_at from a bare date to a full moment.

    ``plan_runs`` is keyed on ``(plan_id, due_at)``, and the text of a keyed
    column is part of the key. Rows written while ``due_at`` was a ``date`` hold
    "2026-01-01"; every row written now holds "2026-01-01T00:00:00". Two
    different strings do not collide, so the invariant that key exists to hold -
    one run per plan per occurrence - would quietly stop holding: a plan whose
    run was recorded BLOCKED at "2026-01-01", later topped up and resumed, would
    append a *second* row for the same occurrence instead of updating the first.

    Only blocked runs make this reachable, which is why it is easy to miss. A
    successful run advances the plan's counter, so its occurrence is never
    re-derived; a blocked run deliberately does not advance it, so the same
    occurrence comes round again. The row that is hardest to notice is the one
    that breaks.

    ``T00:00:00`` is not an arbitrary choice: ``datetime(2026, 1, 1).isoformat()``
    produces exactly this, and a legacy plan's anchor loads as midnight (see
    ``text_to_schedule``), so its occurrences *are* midnight. The rewrite makes
    what is stored agree with what is derived.

    Idempotent through the ``WHERE`` rather than through a ``PRAGMA`` guard -
    unlike the two migrations above, nothing about the *shape* of the table is
    changing, so the only question is whether a row's value has been widened yet.
    Once no row matches, this is a no-op.

    One more thing that makes this safe, and it is not obvious: the migration
    runs on **every** connection open, before that connection can write anything.
    So it is not possible for this version to add a "2026-01-01T00:00:00" row
    while a "2026-01-01" row for the same occurrence is still there - the old row
    is always widened first. Were that ordering reversed, the UPDATE would collide
    with itself on the primary key, which is a loud failure rather than a silent
    one, but a failure this ordering means never happens.

    **Why ``transactions.internal_reference`` is not migrated**, since it also
    embeds ``due_at`` and reads "plan:{id}:2026-01-01:0" for old runs. A
    reference is written only by a run that *succeeded*, and success advances the
    counter in the same transaction that writes the ledger row. So no reference
    can exist for an occurrence the plan is still due for - there is nothing for
    a rewritten key to collide with, and nothing to rewrite. A blocked run writes
    no transaction at all, which is the entire point of the pre-flight check.
    """
    connection.execute(
        """
        UPDATE plan_runs
        SET due_at = due_at || 'T00:00:00'
        WHERE due_at NOT LIKE '%T%'
        """
    )


def _migrate_add_fund_id_column(connection: sqlite3.Connection) -> None:
    """Add transactions.fund_id to a database created before pots existed.

    Which pot a movement went through, when one did. Nullable, and not only
    because ALTER cannot add NOT NULL without a default: a payout in this phase
    legitimately names no pot, and a deposit to the available balance never had
    one. NULL here means "no pot was involved", which is a real answer rather
    than missing data.

    Structurally this is ``_migrate_add_destination_column`` again, and the
    reasoning there applies unchanged - ``CREATE TABLE IF NOT EXISTS`` gives a
    new column to new databases only, so old ones need the explicit ALTER, and
    the PRAGMA guard is what makes re-running on every connection safe.
    """
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(transactions)")
    }
    if "fund_id" not in columns:
        connection.execute("ALTER TABLE transactions ADD COLUMN fund_id TEXT")


#: The first SQLite release with ``ALTER TABLE ... DROP COLUMN`` (2021-03-12).
#: Checked rather than assumed: the linked SQLite is a property of the machine
#: and the Python build, not of this code, so it is not something to be right
#: about on the developer's laptop and wrong on the user's.
_DROP_COLUMN_MINIMUM = (3, 35, 0)


def _migrate_locked_balance_into_funds(connection: sqlite3.Connection) -> None:
    """Move each wallet's locked balance into a pot, then drop the column.

    Before pots, a wallet's locked money was one number in
    ``wallets.locked_balance``. Now it is the sum of that wallet's funds, and the
    column has nowhere to fit - so this is the one migration in the file that
    *removes* something rather than adding it.

    **The migration is invisible on purpose.** Every pot it creates is named
    "Locked", is ``PERSONAL``, and has ``maturity_date = NULL``, which is defined
    to mean "no maturity, always open". So money that was releasable
    unconditionally before this ran is releasable unconditionally after it. A
    user's database does not gain a lock they never asked for; it gains a name
    for money that was already set aside.

    **Why the rows are moved in Python and not by an ``INSERT ... SELECT``.** The
    fund needs a ``fund_id``, and SQLite has no ``uuid()`` function to generate
    one in the SELECT. Reading the rows and inserting them one at a time is what
    lets a real UUID be minted for each.

    **Idempotence comes from the WHERE clause**, not a PRAGMA guard on the shape
    of the table - the same choice ``_migrate_plan_run_due_at_to_datetime`` made,
    and for a sharper reason here. Migrations run in autocommit, before the unit
    of work's transaction begins, so a process killed between the inserts and the
    DROP would leave the column still present with the pots already written. On
    the next start, skipping wallets that already have funds means the inserts do
    nothing rather than colliding with themselves on ``UNIQUE (wallet_id, name)``.
    A failure there would be loud rather than silent, but an app that will not
    start is its own kind of wrong.

    Money decisions are made in Python rather than SQL. ``locked_balance`` is
    stored as text, so "is this balance non-zero?" in SQL would mean either
    comparing strings that may be written "0", "0.00" or "0.000" and are not
    equal, or casting to REAL - and this is the one codebase where reaching for a
    float to answer a question about money should look wrong. "Does this wallet
    already have funds?" is a question about existence, not about money, and SQL
    answers it exactly.
    """
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(wallets)")
    }
    if "locked_balance" not in columns:
        # Already migrated, or a database created fresh from SCHEMA above, which
        # no longer has the column at all. Either way there is nothing to move.
        return

    if sqlite3.sqlite_version_info < _DROP_COLUMN_MINIMUM:
        raise RuntimeError(
            "this database still has wallets.locked_balance and needs "
            f"ALTER TABLE ... DROP COLUMN, which requires SQLite "
            f"{'.'.join(str(part) for part in _DROP_COLUMN_MINIMUM)} or newer; "
            f"this Python is linked against {sqlite3.sqlite_version}. "
            "Either upgrade SQLite, or move each wallet's locked balance into a "
            "fund by hand and drop the column."
        )

    rows = connection.execute(
        """
        SELECT wallet_id, currency, locked_balance
        FROM wallets
        WHERE NOT EXISTS (
            SELECT 1 FROM funds WHERE funds.wallet_id = wallets.wallet_id
        )
        """
    ).fetchall()

    for row in rows:
        locked = text_to_money(
            row["locked_balance"], text_to_enum(Currency, row["currency"])
        )
        if locked.amount <= 0:
            # An empty pot is not a thing to open. A wallet that never locked
            # anything ends up with no funds, which is what it had before.
            continue
        connection.execute(
            """
            INSERT INTO funds
                (fund_id, wallet_id, name, kind, balance, maturity_date, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                uuid_to_text(uuid.uuid4()),
                row["wallet_id"],
                "Locked",
                enum_to_text(FundKind.PERSONAL),
                money_to_text(locked),
                datetime_to_text(datetime.now()),
            ),
        )

    connection.execute("ALTER TABLE wallets DROP COLUMN locked_balance")


def open_sqlite_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection in autocommit mode with the schema applied.

    isolation_level=None switches off sqlite3's implicit transaction handling,
    so transactions are started and ended explicitly (see
    SqliteUnitOfWorkFactory.start).
    """
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    # Every call below is here because it changes something about a table that
    # may *already exist*. Nothing does the same for ``plan_notices`` or
    # ``outbound_messages``, and that absence is deliberate: ``CREATE TABLE IF
    # NOT EXISTS`` above creates a missing table on an existing database quite
    # happily, so a brand-new table arrives for free. Adding a table is not a
    # migration; changing a table that is already on disk is.
    _migrate_add_destination_column(connection)
    _migrate_add_plan_name_column(connection)
    _migrate_plan_run_due_at_to_datetime(connection)
    _migrate_legacy_transaction_types(connection)
    # After the two above and after executescript, which is what has just created
    # the ``funds`` table this migration writes into - it is new, so per the note
    # inside it there is no migration for its creation, only for filling it from
    # the column it replaces.
    _migrate_add_fund_id_column(connection)
    _migrate_locked_balance_into_funds(connection)
    return connection


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self.wallets = SqliteWalletRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)
        self.plans = SqliteSavingsPlanRepository(connection)
        self.plan_runs = SqlitePlanRunRepository(connection)
        self.notices = SqlitePlanNoticeRepository(connection)
        self.notifications = SqliteNotificationRepository(connection)
        self.outbound_messages = SqliteOutboundMessageRepository(connection)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


class SqliteUnitOfWorkFactory:
    """Creates a fresh Unit of Work (own connection + transaction) per call.

    Every start() opens a new connection to the same database file, so
    committed work is visible to later units but no two units ever share a
    connection or a transaction.
    """

    def __init__(self, db_path: str = "budget.db"):
        self.db_path = db_path

    def start(self) -> SqliteUnitOfWork:
        connection = open_sqlite_connection(self.db_path)
        connection.execute("BEGIN")
        return SqliteUnitOfWork(connection)
