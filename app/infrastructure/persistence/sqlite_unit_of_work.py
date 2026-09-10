"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3

from app.application.unit_of_work import UnitOfWork
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
    source         TEXT NOT NULL,
    schedule       TEXT NOT NULL,    -- JSON: cadence name + anchor date
    instructions   TEXT NOT NULL,    -- JSON array; value objects live with their root
    status         TEXT NOT NULL,
    completed_runs INTEGER NOT NULL, -- drift guard: next due = anchor + n, never anchor + 1
    ends_on        TEXT,             -- ISO date; NULL means open-ended
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_runs (
    plan_id     TEXT NOT NULL REFERENCES savings_plans(plan_id),
    due_at      TEXT NOT NULL,   -- ISO date of the occurrence this run is for
    status      TEXT NOT NULL,
    reason      TEXT,            -- enum name; blocked runs only, NULL otherwise
    recorded_at TEXT NOT NULL,
    -- The natural key, and the whole reason a retry is coherent: one plan has
    -- at most one run per occurrence, so re-saving rewrites rather than
    -- appends. A run cannot be identified by a generated id because it was
    -- never anything but (plan, occurrence).
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
    _migrate_add_destination_column(connection)
    _migrate_legacy_transaction_types(connection)
    return connection


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self.wallets = SqliteWalletRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)
        self.plans = SqliteSavingsPlanRepository(connection)
        self.plan_runs = SqlitePlanRunRepository(connection)

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
