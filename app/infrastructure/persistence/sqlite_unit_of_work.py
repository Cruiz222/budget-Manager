"""SQLite-backed Unit of Work.

One SqliteUnitOfWork is one connection with an open transaction. The service
opens one per business operation: the reads and writes made through its two
repositories all land in that single transaction and become durable together on
commit() - or are all discarded by rollback().
"""

import sqlite3

from app.application.unit_of_work import UnitOfWork
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
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    reversed_at        TEXT
);
"""


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
    return connection


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self.wallets = SqliteWalletRepository(connection)
        self.transactions = SqliteTransactionRepository(connection)

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
