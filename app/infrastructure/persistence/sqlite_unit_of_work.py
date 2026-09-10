"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3

from app.application.unit_of_work import UnitOfWork
from app.infrastructure.repositories.sqlite_plan_notice_repository import (
    SqlitePlanNoticeRepository,
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
    available_balance TEXT NOT NULL,   -- decimal as text, e.g. "10000.00"
    locked_balance    TEXT NOT NULL
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
    return connection


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self.wallets = SqliteWalletRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)
        self.plans = SqliteSavingsPlanRepository(connection)
        self.plan_runs = SqlitePlanRunRepository(connection)
        self.notices = SqlitePlanNoticeRepository(connection)
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
