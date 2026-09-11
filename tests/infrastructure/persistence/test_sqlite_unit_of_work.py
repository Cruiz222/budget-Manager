from decimal import Decimal
from uuid import uuid4

import pytest
import sqlite3

from app.domain.money.currency import Currency
from app.domain.money.exception import WalletNotFoundError
from app.domain.money.fundKind import FundKind
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


#: The wallets table exactly as it looked while ``locked_balance`` was a stored
#: column - that is, before funds existed. Written out rather than derived, for
#: the same reason ``LEGACY_TRANSACTIONS_SCHEMA`` is: the point of these tests is
#: to simulate a real database that predates the change.
LEGACY_WALLETS_SCHEMA = """
CREATE TABLE wallets (
    wallet_id         TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL,
    available_balance TEXT NOT NULL,
    locked_balance    TEXT NOT NULL
);
"""


def build_legacy_database(db_path, rows):
    """A pre-funds database holding the given wallets, ready to be opened."""
    legacy = sqlite3.connect(db_path, isolation_level=None)
    legacy.executescript(LEGACY_TRANSACTIONS_SCHEMA)
    legacy.executescript(LEGACY_WALLETS_SCHEMA)
    legacy.executemany(
        "INSERT INTO wallets VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    legacy.close()


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


# --- the migration from a stored locked balance to pots --------------------


WALLET_ID = "11111111-1111-1111-1111-111111111111"
OTHER_WALLET_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"


def legacy_wallet_row(wallet_id, locked, available="10000.00"):
    return (wallet_id, USER_ID, "NGN", "ACTIVE", available, locked)


def test_a_locked_balance_is_migrated_into_a_pot(tmp_path):
    """The shape of the one migration that removes a column.

    Asserted on all three facts at once, because any two of them without the
    third is a broken database: the pot exists and holds the money, the wallet
    still reports the same locked balance through it, and the column that used to
    hold it is gone.
    """
    db_path = str(tmp_path / "legacy_balance.db")
    build_legacy_database(db_path, [legacy_wallet_row(WALLET_ID, "4000.00")])

    connection = open_sqlite_connection(db_path)
    connection.close()

    reopened = open_sqlite_connection(db_path)
    try:
        funds = reopened.execute(
            "SELECT name, kind, balance, maturity_date FROM funds"
        ).fetchall()
        columns = {
            row["name"] for row in reopened.execute("PRAGMA table_info(wallets)")
        }
    finally:
        reopened.close()

    assert len(funds) == 1
    assert funds[0]["name"] == "Locked"
    assert funds[0]["kind"] == "PERSONAL"
    assert funds[0]["balance"] == "4000.00"
    # NULL is "no maturity, always open" - which is what makes the migration
    # invisible. Money that was releasable unconditionally stays so.
    assert funds[0]["maturity_date"] is None
    assert "locked_balance" not in columns

    stored = SqliteUnitOfWorkFactory(db_path).start()
    try:
        wallet = stored.wallets.get_by_id(WALLET_ID)
        assert wallet.locked_balance == Money(Decimal("4000.00"), NGN)
        assert wallet.available_balance == Money(Decimal("10000.00"), NGN)
        assert wallet.fund_by_name("Locked").is_open
    finally:
        stored.rollback()


def test_a_wallet_that_never_locked_anything_gains_no_pot(tmp_path):
    """A zero balance migrates to nothing at all, rather than to an empty pot.

    Two reasons, and the second is the one worth writing down. An empty pot is
    noise in a listing - and it would change the *shape* of the wallet for every
    user who never locked anything, which is most of them. ``build_wallet`` makes
    the same choice for the same reason.
    """
    db_path = str(tmp_path / "legacy_zero.db")
    build_legacy_database(db_path, [legacy_wallet_row(WALLET_ID, "0.00")])

    SqliteUnitOfWorkFactory(db_path).start().rollback()

    stored = SqliteUnitOfWorkFactory(db_path).start()
    try:
        assert stored.wallets.get_by_id(WALLET_ID).funds == ()
    finally:
        stored.rollback()


def test_each_wallet_gets_its_own_pot(tmp_path):
    """The migration is per wallet, not per database.

    A single shared pot would be caught nowhere else: the totals would be wrong
    only for wallets whose neighbours held a different amount, and the pot would
    belong to whichever wallet the query returned first.
    """
    db_path = str(tmp_path / "legacy_two.db")
    build_legacy_database(
        db_path,
        [
            legacy_wallet_row(WALLET_ID, "4000.00"),
            legacy_wallet_row(OTHER_WALLET_ID, "1750.50"),
        ],
    )

    SqliteUnitOfWorkFactory(db_path).start().rollback()

    stored = SqliteUnitOfWorkFactory(db_path).start()
    try:
        first = stored.wallets.get_by_id(WALLET_ID)
        second = stored.wallets.get_by_id(OTHER_WALLET_ID)
        assert first.locked_balance == Money(Decimal("4000.00"), NGN)
        assert second.locked_balance == Money(Decimal("1750.50"), NGN)
        assert first.funds[0].fund_id != second.funds[0].fund_id
    finally:
        stored.rollback()


def test_running_the_migration_twice_changes_nothing(tmp_path):
    """Idempotence, and the crash it is there to survive.

    Migrations run in autocommit *before* the unit of work's transaction, so a
    process killed between the inserts and the DROP leaves pots written and the
    column still standing. The next start has to cope - and the ``WHERE NOT
    EXISTS`` does, where a PRAGMA guard on the column would have tried to insert
    the pot again and collided on ``UNIQUE (wallet_id, name)``.
    """
    db_path = str(tmp_path / "legacy_twice.db")
    build_legacy_database(db_path, [legacy_wallet_row(WALLET_ID, "4000.00")])

    for _ in range(3):
        SqliteUnitOfWorkFactory(db_path).start().rollback()

    connection = open_sqlite_connection(db_path)
    try:
        funds = connection.execute("SELECT name, balance FROM funds").fetchall()
    finally:
        connection.close()

    assert len(funds) == 1
    assert funds[0]["balance"] == "4000.00"


def test_a_partially_migrated_database_is_not_double_credited(tmp_path):
    """The crash window itself, staged deliberately.

    The pots are written, the DROP has not run, and the column still says 4000.
    Running the migration from here must leave one pot holding 4000 - not two,
    and not one holding 8000. This is the state the ``WHERE NOT EXISTS`` exists
    for, and it is otherwise reachable only by killing the process at exactly the
    wrong moment.
    """
    db_path = str(tmp_path / "halfway.db")
    build_legacy_database(db_path, [legacy_wallet_row(WALLET_ID, "4000.00")])

    connection = open_sqlite_connection(db_path)
    connection.close()
    # Put the column back, as a crash before the DROP would have left it.
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.execute(
        "ALTER TABLE wallets ADD COLUMN locked_balance TEXT NOT NULL DEFAULT '4000.00'"
    )
    connection.close()

    SqliteUnitOfWorkFactory(db_path).start().rollback()

    connection = open_sqlite_connection(db_path)
    try:
        funds = connection.execute("SELECT balance FROM funds").fetchall()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(wallets)")
        }
    finally:
        connection.close()

    assert len(funds) == 1
    assert funds[0]["balance"] == "4000.00"
    assert "locked_balance" not in columns


def test_the_column_drop_refuses_an_old_sqlite_rather_than_corrupting(tmp_path, monkeypatch):
    """The guard is a runtime check, so it can be exercised on any machine.

    ``ALTER TABLE ... DROP COLUMN`` arrived in SQLite 3.35.0, and the linked
    SQLite is a property of the machine and the Python build rather than of this
    code. Raising a message that names the requirement is the alternative to
    half-migrating a database on an older install, so the failure path is worth a
    test rather than a comment - and monkeypatching the version is what makes it
    testable without an old SQLite to hand.
    """
    db_path = str(tmp_path / "ancient.db")
    build_legacy_database(db_path, [legacy_wallet_row(WALLET_ID, "4000.00")])

    # A context, not a bare setattr: ``monkeypatch.undo()`` would also undo the
    # suite-wide fixture that clears the notification environment, and this test
    # has no business restoring that.
    with monkeypatch.context() as patched:
        patched.setattr(sqlite3, "sqlite_version_info", (3, 34, 0))
        with pytest.raises(RuntimeError, match="SQLite 3.35.0 or newer"):
            open_sqlite_connection(db_path)

    # And nothing was half-done on the way to refusing: the column is still
    # there and no pot has been written. The version check comes before the
    # inserts precisely so that this is true.
    #
    # Read with a *raw* connection, not ``open_sqlite_connection``. That function
    # is the thing under test - it would run the migration for real now that the
    # version is restored, and quietly complete the very work this asserts did
    # not happen. The database is opened directly so that the state the refusal
    # left behind can be looked at rather than advanced past.
    raw = sqlite3.connect(db_path)
    try:
        written = raw.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
        columns = {row[1] for row in raw.execute("PRAGMA table_info(wallets)")}
    finally:
        raw.close()

    assert written == 0
    # The legacy column is still standing, so the database is exactly as it was
    # found. That is the point of refusing: an install that cannot complete the
    # migration keeps a working database rather than a half-migrated one.
    assert "locked_balance" in columns


def test_a_transactions_table_predating_fund_id_is_migrated(tmp_path):
    """The same ALTER-and-PRAGMA pattern as ``destination``, on a new column."""
    db_path = str(tmp_path / "legacy_fund_id.db")
    legacy = sqlite3.connect(db_path, isolation_level=None)
    legacy.executescript(LEGACY_TRANSACTIONS_SCHEMA)
    legacy.close()

    connection = open_sqlite_connection(db_path)
    try:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(transactions)")
        }
    finally:
        connection.close()

    assert "fund_id" in columns


def test_a_transactions_fund_id_survives_a_round_trip(tmp_path, build_wallet):
    """The column is written and read back, not merely created.

    A migration test alone would pass on a column that no code ever populates, so
    the round trip is asserted here alongside it.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "fund_id.db"))
    wallet = build_wallet(available="1000")
    fund = wallet.open_fund("Vacation", FundKind.PERSONAL)
    transaction = Transaction(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("500"), NGN),
        internal_reference=str(uuid4()),
        fund_id=fund.fund_id,
    )
    transaction.mark_successful()

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(transaction)
    uow.commit()

    fresh = factory.start()
    try:
        stored = fresh.transactions.get_by_id(transaction.transaction_id)
        assert stored.fund_id == fund.fund_id
    finally:
        fresh.rollback()


def test_a_transaction_with_no_pot_round_trips_as_none(tmp_path, build_wallet):
    """NULL means "no pot was involved", which is a real answer.

    A row for a plain deposit never had a pot, and reading it back must not
    invent one.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "no_fund_id.db"))
    wallet = build_wallet()
    transaction = build_successful_deposit(wallet, str(uuid4()))

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.transactions.save(transaction)
    uow.commit()

    fresh = factory.start()
    try:
        stored = fresh.transactions.get_by_id(transaction.transaction_id)
        assert stored.fund_id is None
    finally:
        fresh.rollback()
