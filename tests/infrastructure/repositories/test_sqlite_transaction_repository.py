from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import TransactionNotFoundError
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.persistence.sqlite_unit_of_work import open_sqlite_connection
from app.infrastructure.repositories.sqlite_transaction_repository import (
    SqliteTransactionRepository,
)
from app.infrastructure.repositories.sqlite_wallet_repository import (
    SqliteWalletRepository,
)

NGN = Currency.NGN


def build_wallet():
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=WalletStatus.ACTIVE,
        _available_balance=Money(Decimal("10000"), NGN),
        _locked_balance=Money(Decimal("0"), NGN),
        currency=NGN,
    )


def build_transaction(wallet, **overrides):
    kwargs = dict(
        wallet_id=wallet.wallet_id,
        type=TransactionType.DEPOSIT,
        amount=Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )
    kwargs.update(overrides)
    return Transaction(**kwargs)


def build_repository(wallet):
    connection = open_sqlite_connection(":memory:")
    # Seed the wallet row so the transaction's foreign key resolves.
    SqliteWalletRepository(connection).save(wallet)
    return SqliteTransactionRepository(connection)


def test_round_trips_a_successful_transaction():
    wallet = build_wallet()
    transaction = build_transaction(
        wallet,
        provider_reference="prov-1",
        narration="first deposit",
        metadata={"channel": "web"},
    )
    transaction.mark_successful()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.transaction_id == transaction.transaction_id
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.DEPOSIT
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.amount == Money(Decimal("5000"), NGN)
    assert stored.internal_reference == transaction.internal_reference
    assert stored.provider_reference == "prov-1"
    assert stored.narration == "first deposit"
    assert dict(stored.metadata) == {"channel": "web"}
    assert stored.created_at == transaction.created_at
    assert stored.completed_at == transaction.completed_at


def test_save_updates_the_same_row_across_the_lifecycle():
    """A PENDING write followed by a SUCCESSFUL write must update one row."""
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    repository = build_repository(wallet)

    repository.save(transaction)  # the PENDING record of intent
    transaction.mark_successful()
    repository.save(transaction)  # the terminal state

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    count = repository._connection.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize(
    ("transition", "expected_status"),
    [
        ("mark_successful", TransactionStatus.SUCCESSFUL),
        ("mark_failed", TransactionStatus.FAILED),
    ],
)
def test_round_trips_terminal_status(transition, expected_status):
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    getattr(transaction, transition)()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is expected_status
    assert stored.completed_at == transaction.completed_at


def test_round_trips_a_reversed_transaction():
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    transaction.mark_successful()
    transaction.reverse()
    repository = build_repository(wallet)

    repository.save(transaction)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.REVERSED
    assert stored.completed_at == transaction.completed_at
    assert stored.reversed_at == transaction.reversed_at


def test_get_by_internal_reference_finds_a_saved_transaction():
    wallet = build_wallet()
    transaction = build_transaction(wallet)
    transaction.mark_successful()
    repository = build_repository(wallet)
    repository.save(transaction)

    stored = repository.get_by_internal_reference(transaction.internal_reference)

    assert stored is not None
    assert stored.transaction_id == transaction.transaction_id


def test_get_by_internal_reference_returns_none_when_absent():
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_internal_reference(str(uuid4())) is None


def test_get_by_wallet_id_returns_that_wallets_ledger_oldest_first():
    wallet = build_wallet()
    other_wallet = build_wallet()
    repository = build_repository(wallet)
    # Seed the second wallet so the foreign key resolves.
    SqliteWalletRepository(repository._connection).save(other_wallet)

    first = build_transaction(wallet, internal_reference=str(uuid4()))
    second = build_transaction(wallet, internal_reference=str(uuid4()))
    stranger = build_transaction(other_wallet, internal_reference=str(uuid4()))
    for transaction in (first, second, stranger):
        transaction.mark_successful()
        repository.save(transaction)

    ledger = repository.get_by_wallet_id(wallet.wallet_id)

    assert [t.transaction_id for t in ledger] == [
        first.transaction_id,
        second.transaction_id,
    ]


def test_get_by_wallet_id_is_empty_for_a_wallet_without_transactions():
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_wallet_id(wallet.wallet_id) == []


def test_get_by_provider_reference_finds_a_saved_transaction():
    wallet = build_wallet()
    transaction = build_transaction(wallet, provider_reference="prov-123")
    transaction.mark_successful()
    repository = build_repository(wallet)
    repository.save(transaction)

    stored = repository.get_by_provider_reference("prov-123")

    assert stored is not None
    assert stored.transaction_id == transaction.transaction_id


def test_get_by_provider_reference_returns_none_when_absent():
    wallet = build_wallet()
    repository = build_repository(wallet)

    assert repository.get_by_provider_reference("prov-missing") is None


def test_get_by_id_of_missing_transaction_raises():
    wallet = build_wallet()
    repository = build_repository(wallet)

    with pytest.raises(TransactionNotFoundError):
        repository.get_by_id(uuid4())
