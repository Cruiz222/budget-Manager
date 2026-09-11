from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

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
from app.infrastructure.persistence.serialization import (
    datetime_to_text,
    enum_to_text,
    instructions_to_text,
    schedule_to_text,
    uuid_to_text,
)
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


# --- the migration that lets a commitment be judged against the money -----


#: The ``wallets`` table after pots existed but before a commitment could be
#: judged against one. Deliberately *without* ``locked_balance``, so the pot
#: rows below are the only thing the migration under test has to look at and the
#: older locked-balance migration stays out of the way.
LEGACY_WALLETS_AFTER_POTS_SCHEMA = """
CREATE TABLE wallets (
    wallet_id         TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL,
    available_balance TEXT NOT NULL
);
"""


#: The ``funds`` and ``savings_plans`` tables exactly as they looked before
#: Phase B. Two differences, and they are the whole subject: a pot carried no
#: ``sealed_at`` and no ``first_funded_at``, and a plan carried no ``fund_id``.
#: Written out rather than derived, for the reason the other legacy schemas are:
#: these tests exist to simulate a database a real earlier release left behind.
LEGACY_POTS_SCHEMA = """
CREATE TABLE funds (
    fund_id       TEXT PRIMARY KEY,
    wallet_id     TEXT NOT NULL REFERENCES wallets(wallet_id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    balance       TEXT NOT NULL,
    maturity_date TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (wallet_id, name)
);

CREATE TABLE savings_plans (
    plan_id        TEXT PRIMARY KEY,
    wallet_id      TEXT NOT NULL REFERENCES wallets(wallet_id),
    name           TEXT NOT NULL,
    source         TEXT NOT NULL,
    schedule       TEXT NOT NULL,
    instructions   TEXT NOT NULL,
    status         TEXT NOT NULL,
    completed_runs INTEGER NOT NULL,
    ends_on        TEXT,
    created_at     TEXT NOT NULL
);
"""

#: One moment for every legacy row, so "which timestamp did the backfill use?"
#: has an answer that a swapped column cannot also produce.
LEGACY_MOMENT = datetime(2025, 6, 1, 12, 0)


def legacy_pot_row(fund_id, wallet_id, name, balance, kind="PERSONAL"):
    return (
        fund_id,
        wallet_id,
        name,
        kind,
        balance,
        None,
        datetime_to_text(LEGACY_MOMENT),
    )


def build_pre_phase_b_database(db_path, pots, plans=()):
    """A database written by the release before a plan could name a pot.

    Already pot-shaped - the wallets table has no ``locked_balance``, so the
    migration that moves one into a pot is a no-op and stays out of the way -
    but written before a pot recorded when it was sealed or first funded.

    Returns the raw connection, not a unit of work, because the caller is
    usually about to write rows the *current* repository could not: a plan with
    no ``fund_id`` column to write to.
    """
    legacy = sqlite3.connect(db_path, isolation_level=None)
    legacy.executescript(LEGACY_WALLETS_AFTER_POTS_SCHEMA)
    legacy.executescript(LEGACY_POTS_SCHEMA)
    legacy.execute(
        "INSERT INTO wallets VALUES (?, ?, ?, ?, ?)",
        (WALLET_ID, USER_ID, "NGN", "ACTIVE", "10000.00"),
    )
    legacy.executemany(
        "INSERT INTO funds VALUES (?, ?, ?, ?, ?, ?, ?)",
        [legacy_pot_row(*pot) for pot in pots],
    )
    legacy.executemany(
        """
        INSERT INTO savings_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        plans,
    )
    legacy.close()


def raw_columns(connection, table):
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_a_pots_commitment_columns_arrive_on_an_old_database(tmp_path):
    """Two columns on a table that already had rows, which is what needs a migration.

    ``CREATE TABLE IF NOT EXISTS`` gives a new column to a new database only, so
    every database that predates the rule is missing both - and the rule cannot
    be judged at all until they exist. The plan's ``fund_id`` is asserted in the
    same breath because it is the other half of the same phase and the same
    failure: a plan that cannot record which pot it committed to cannot make a
    commitment that means anything.
    """
    db_path = str(tmp_path / "pre_phase_b.db")
    build_pre_phase_b_database(db_path, [])

    connection = open_sqlite_connection(db_path)
    try:
        funds = raw_columns(connection, "funds")
        plans = raw_columns(connection, "savings_plans")
    finally:
        connection.close()

    assert {"sealed_at", "first_funded_at"} <= funds
    assert "fund_id" in plans


def test_the_backfill_keeps_an_empty_pot_unfunded(tmp_path):
    """The distinction the backfill exists to preserve, in one table.

    Two pots, one holding money and one holding none, and they must come out of
    the migration telling different stories. Both are sealed when they were
    created - a pot's date is as old as the pot - but only the funded one has a
    ``first_funded_at``, because only it has ever received money. Giving the
    empty pot one would be a lie with a consequence: it would date a funding that
    never happened.
    """
    db_path = str(tmp_path / "backfill.db")
    build_pre_phase_b_database(
        db_path,
        [
            ("aaaaaaaa-0000-0000-0000-000000000001", WALLET_ID, "Supplier", "4000.00", "BUSINESS"),
            ("aaaaaaaa-0000-0000-0000-000000000002", WALLET_ID, "Holiday", "0.00"),
        ],
    )

    connection = open_sqlite_connection(db_path)
    try:
        rows = {
            row["name"]: row
            for row in connection.execute(
                "SELECT name, sealed_at, first_funded_at, created_at FROM funds"
            )
        }
    finally:
        connection.close()

    assert rows["Supplier"]["sealed_at"] == datetime_to_text(LEGACY_MOMENT)
    assert rows["Supplier"]["first_funded_at"] == datetime_to_text(LEGACY_MOMENT)
    # And the empty pot, which is the whole point of the test.
    assert rows["Holiday"]["sealed_at"] == datetime_to_text(LEGACY_MOMENT)
    assert rows["Holiday"]["first_funded_at"] is None


@pytest.mark.parametrize("zero", ["0", "0.00", "0.000"])
def test_an_empty_pot_is_recognised_however_its_zero_was_written(tmp_path, zero):
    """``balance`` is text, and three different strings all mean nothing.

    A string comparison against "0.00" would migrate two of these three pots into
    a funding moment they never had, and the pot that got through would be the
    one whose balance happened to be formatted the other way. The migration casts
    instead, which is the one place in this codebase that touching a money column
    with SQL arithmetic is defensible: the question is "is this zero?", and the
    answer is thrown away.
    """
    db_path = str(tmp_path / f"zero_{zero.replace('.', '_')}.db")
    build_pre_phase_b_database(
        db_path,
        [("aaaaaaaa-0000-0000-0000-000000000002", WALLET_ID, "Holiday", zero)],
    )

    connection = open_sqlite_connection(db_path)
    try:
        row = connection.execute(
            "SELECT first_funded_at FROM funds WHERE name = 'Holiday'"
        ).fetchone()
    finally:
        connection.close()

    assert row["first_funded_at"] is None


def test_the_backfill_runs_once(tmp_path):
    """Opening an old database three times leaves it exactly as the first open did.

    Migrations run in autocommit before the unit of work's transaction, so they
    are re-run on every connection for the life of the database. An UPDATE
    guarded on ``IS NULL`` and on a non-zero balance is stable: the funded pot's
    anchor is set and no longer NULL, and the empty pot's is NULL *and* its
    balance is still zero, so neither is touched again.
    """
    db_path = str(tmp_path / "backfill_twice.db")
    build_pre_phase_b_database(
        db_path,
        [
            ("aaaaaaaa-0000-0000-0000-000000000001", WALLET_ID, "Supplier", "4000.00", "BUSINESS"),
            ("aaaaaaaa-0000-0000-0000-000000000002", WALLET_ID, "Holiday", "0.00"),
        ],
    )

    first = open_sqlite_connection(db_path)
    try:
        before = [
            tuple(row)
            for row in first.execute(
                "SELECT fund_id, sealed_at, first_funded_at, created_at FROM funds ORDER BY fund_id"
            )
        ]
    finally:
        first.close()

    for _ in range(3):
        again = open_sqlite_connection(db_path)
        again.close()

    final = open_sqlite_connection(db_path)
    try:
        after = [
            tuple(row)
            for row in final.execute(
                "SELECT fund_id, sealed_at, first_funded_at, created_at FROM funds ORDER BY fund_id"
            )
        ]
    finally:
        final.close()

    assert after == before


def test_a_plan_saved_before_pots_could_be_named_still_hydrates(
    tmp_path, build_plan
):
    """The legacy plan, read back through the real repository.

    A column added to a table reads back as ``None`` on an old row, and ``None``
    is precisely "this plan predates naming, draw on the pool as it always did" -
    so the migration is invisible: the plan hydrates, its instructions survive,
    and ``ExecutePlanRun`` will hand it the pooled draw it has always had.

    The row is written with the real serializers rather than hand-typed JSON,
    because a hand-typed string would test the migration against a shape no
    release ever produced. The wallet id is ``WALLET_ID`` rather than a fresh
    uuid because the row below is supposed to belong to the wallet the legacy
    database already holds - a plan pointing at a wallet that does not exist
    would be a database no release could have written.
    """
    db_path = str(tmp_path / "legacy_plan.db")
    plan = build_plan(wallet_id=UUID(WALLET_ID))
    build_pre_phase_b_database(
        db_path,
        [],
        plans=[
            (
                uuid_to_text(plan.plan_id),
                uuid_to_text(plan.wallet_id),
                plan.name,
                enum_to_text(plan.source),
                schedule_to_text(plan.schedule),
                instructions_to_text(plan.instructions),
                enum_to_text(plan.status),
                plan.completed_runs,
                None,
                datetime_to_text(plan.created_at),
            )
        ],
    )

    fresh = SqliteUnitOfWorkFactory(db_path).start()
    try:
        stored = fresh.plans.get_by_id(plan.plan_id)
        assert stored.fund_id is None
        assert stored.instructions == plan.instructions
        assert stored.source is plan.source
    finally:
        fresh.rollback()


def test_a_pot_round_trips_its_two_moments(tmp_path, build_wallet):
    """The columns are written and read back, not merely created by a migration.

    Asserted against the pot that was saved rather than against a fixed moment,
    which is the stronger form here: it pins "what went in comes out" without the
    test having to know what the fixture chose - and a test that knew would still
    pass if both ends were wrong in the same way.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "pot_moments.db"))
    wallet = build_wallet(locked="4000")
    pot = wallet.fund_by_name("Locked")

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.commit()

    fresh = factory.start()
    try:
        stored = fresh.wallets.get_by_id(wallet.wallet_id).fund_by_name("Locked")
        assert stored.sealed_at == pot.sealed_at
        assert stored.first_funded_at == pot.first_funded_at
        assert stored.first_funded_at is not None
    finally:
        fresh.rollback()


def test_an_empty_pot_round_trips_as_never_funded(tmp_path, build_wallet):
    """``first_funded_at`` is nullable on the way out as well as in.

    The direction that would be easy to get wrong silently: a repository reading
    a NULL moment as ``datetime.min`` would make every empty pot look as though it
    had been funded since the beginning of time - which is the *permissive*
    direction, and would hand the exemption to a pot that has never held a naira.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "empty_pot.db"))
    wallet = build_wallet()
    wallet.open_fund("Supplier", FundKind.BUSINESS, as_of=datetime(2026, 1, 1))

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.commit()

    fresh = factory.start()
    try:
        stored = fresh.wallets.get_by_id(wallet.wallet_id).fund_by_name("Supplier")
        assert stored.first_funded_at is None
        assert stored.balance == Money(Decimal("0"), NGN)
    finally:
        fresh.rollback()


def test_a_plans_pot_round_trips(tmp_path, build_wallet, build_plan):
    """Which pot a plan committed to is a fact the database keeps.

    The half of the phase a plan's own tests cannot see. ``SavingsPlan`` holds a
    ``fund_id`` and refuses the wrong source, but whether it survives a save and
    a reload is a property of the repository - and a plan whose pot vanished on
    the way through would silently fall back to the pooled draw, which is exactly
    the behaviour this phase removes.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "plan_pot.db"))
    wallet = build_wallet(locked="4000")
    pot_id = wallet.fund_by_name("Locked").fund_id
    plan = build_plan(wallet_id=wallet.wallet_id, fund_id=pot_id)

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.plans.save(plan)
    uow.commit()

    fresh = factory.start()
    try:
        stored = fresh.plans.get_by_id(plan.plan_id)
        assert stored.fund_id == pot_id
    finally:
        fresh.rollback()


def test_a_legacy_plans_pot_round_trips_as_none(tmp_path, build_wallet, build_plan):
    """And the other direction, which is what keeps every existing plan working.

    ``None`` is not missing data here - it is the legacy pooled draw, and an
    available-source plan's permanent answer. A repository that invented a pot for
    it would commit money the user never committed.
    """
    factory = SqliteUnitOfWorkFactory(str(tmp_path / "legacy_plan_none.db"))
    wallet = build_wallet(locked="4000")
    plan = build_plan(wallet_id=wallet.wallet_id, fund_id=None)

    uow = factory.start()
    uow.wallets.save(wallet)
    uow.plans.save(plan)
    uow.commit()

    fresh = factory.start()
    try:
        assert fresh.plans.get_by_id(plan.plan_id).fund_id is None
    finally:
        fresh.rollback()
