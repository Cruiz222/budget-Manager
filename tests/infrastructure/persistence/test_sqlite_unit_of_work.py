from decimal import Decimal
from uuid import uuid4

import pytest
import sqlite3

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import (
    SqliteUnitOfWorkFactory,
    open_sqlite_connection,
)

NGN = Currency.NGN

#: The transactions table exactly as it looked before `destination` was added.
#: Written out rather than derived, because the point of the test is to simulate
#: a real database that predates the column.
LEGACY_TRANSACTIONS_SCHEMA = """
CREATE TABLE transactions (
    transaction_id     TEXT PRIMARY KEY,
    wallet_id          TEXT NOT NULL,
    type               TEXT NOT NULL,
    amount             TEXT NOT NULL,
    currency           TEXT NOT NULL,
    internal_reference TEXT NOT NULL UNIQUE,
    provider_reference TEXT,
    narration          TEXT,
    metadata           TEXT,
    status             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    reversed_at        TEXT
);
"""


def build_successful_deposit(wallet, internal_reference):
    transaction = Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )
    transaction.mark_successful()
    return transaction


def build_successful_unlock(wallet, internal_reference):
    transaction = Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.UNLOCK_FUNDS,
        amount=Money(Decimal("5000"), NGN),
        internal_reference=internal_reference,
    )
    transaction.mark_successful()
    return transaction


def test_legacy_scheduled_release_rows_are_migrated_on_open(tmp_path, build_wallet):
    """A database written before the rename must still hydrate.

    ``enum_to_text`` stores the enum member's *name*, so rows predating the
    SCHEDULED_RELEASE -> UNLOCK_FUNDS rename hold the old string. Opening the
    database rewrites them; without that, ``text_to_enum`` would raise KeyError
    on the next read.
    """
    db_path = str(tmp_path / "legacy.db")
    wallet = build_wallet()

    first = SqliteUnitOfWorkFactory(db_path).start()
    first.wallets.save(wallet)
    first.transactions.save(build_successful_unlock(wallet, str(uuid4())))
    first.commit()

    # Rewind the row to how the pre-rename code would have written it.
    connection = open_sqlite_connection(db_path)
    connection.execute(
        "UPDATE transactions SET type = 'SCHEDULED_RELEASE' WHERE type = 'UNLOCK_FUNDS'"
    )
    connection.close()

    # Opening again runs the migration, so the old row becomes readable.
    reopened = open_sqlite_connection(db_path)
    migrated = reopened.execute("SELECT type FROM transactions").fetchone()
    assert migrated["type"] == "UNLOCK_FUNDS"
    reopened.close()

    stored = SqliteUnitOfWorkFactory(db_path).start()
    try:
        ledger = stored.transactions.get_by_wallet_id(wallet.wallet_id)
        assert [transaction.type for transaction in ledger] == [
            TransactionType.UNLOCK_FUNDS
        ]
    finally:
        stored.rollback()


def test_a_database_predating_the_destination_column_is_migrated(tmp_path):
    """CREATE TABLE IF NOT EXISTS does not add a column to an existing table.

    So a new column in SCHEMA reaches brand-new databases only; a database
    already on disk needs an explicit ALTER, which is what this pins.
    """
    db_path = str(tmp_path / "legacy_schema.db")

    legacy = sqlite3.connect(db_path, isolation_level=None)
    legacy.executescript(LEGACY_TRANSACTIONS_SCHEMA)
    legacy.close()

    connection = open_sqlite_connection(db_path)
    try:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(transactions)")
        }
        assert "destination" in columns
    finally:
        connection.close()


def test_rollback_discards_a_wallet_and_its_transaction(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()
    internal_reference = str(uuid4())

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(build_successful_deposit(wallet, internal_reference))
    uow.rollback()

    # A fresh unit - a new connection - must see neither write.
    fresh = factory.start()
    with pytest.raises(WalletNotFoundError):
        fresh.wallets.get_by_id(wallet.wallet_id)
    assert fresh.transactions.get_by_internal_reference(internal_reference) is None
    fresh.rollback()


def test_commit_persists_wallet_and_transaction_together(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()
    wallet.apply_deposit(Money(Decimal("5000"), NGN))  # new balance: 15000
    internal_reference = str(uuid4())

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(build_successful_deposit(wallet, internal_reference))
    uow.commit()

    fresh = factory.start()
    stored_wallet = fresh.wallets.get_by_id(wallet.wallet_id)
    assert stored_wallet.available_balance == Money(Decimal("15000"), NGN)
    stored_transaction = fresh.transactions.get_by_internal_reference(
        internal_reference
    )
    assert stored_transaction is not None
    assert stored_transaction.status is TransactionStatus.SUCCESSFUL
    fresh.rollback()


def test_rollback_keeps_the_prior_committed_balance(tmp_path, build_wallet):
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "atomic.db"))
    wallet = build_wallet()

    first = factory.start()
    first.wallets.save(wallet)
    first.commit()

    # Second unit: a deposit that is then abandoned mid-operation.
    second = factory.start()
    stored = second.wallets.get_by_id(wallet.wallet_id)
    stored.apply_deposit(Money(Decimal("5000"), NGN))
    second.wallets.save(stored)
    second.rollback()

    fresh = factory.start()
    assert (
        fresh.wallets.get_by_id(wallet.wallet_id).available_balance
        == Money(Decimal("10000"), NGN)
    )
    fresh.rollback()
