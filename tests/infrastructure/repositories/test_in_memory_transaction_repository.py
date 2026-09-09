from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.infrastructure.repositories.in_memory_transaction_repository import InMemoryTransactionRepository
from app.domain.money.exception import (
    TransactionNotFoundError
)


def test_save_in_memory_transaction_():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),

    )

    repository = InMemoryTransactionRepository()

    result = repository.save(transaction)

    assert result == transaction

    assert repository.transactions[transaction.transaction_id] == transaction


def test_get_by_id_return_same_transaction():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),

    )

    repository = InMemoryTransactionRepository()

    repository.save(transaction)

    result = repository.get_by_id(transaction.transaction_id)

    assert result == transaction



def test_get_by_invalid_id_raises_error():
   repository = InMemoryTransactionRepository()

   transaction_id = uuid4()

   with pytest.raises(TransactionNotFoundError):
    repository.get_by_id(transaction_id)


def test_get_by_internal_reference_returns_saved_transaction():
    transaction = Transaction(
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="payment-1",
    )

    repository = InMemoryTransactionRepository()

    repository.save(transaction)

    result = repository.get_by_internal_reference("payment-1")

    assert result is transaction


def test_get_by_internal_reference_returns_none_when_not_found():
    repository = InMemoryTransactionRepository()

    result = repository.get_by_internal_reference("unknown-reference")

    assert result is None


def test_get_by_wallet_id_returns_that_wallets_ledger_oldest_first():
    wallet_id = uuid4()
    other_wallet_id = uuid4()

    def build_transaction(owner, created_at):
        return Transaction(
            wallet_id=owner,
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            created_at=created_at,
        )

    repository = InMemoryTransactionRepository()
    first = build_transaction(wallet_id, datetime(2026, 1, 2))
    second = build_transaction(wallet_id, datetime(2026, 1, 3))
    stranger = build_transaction(other_wallet_id, datetime(2026, 1, 1))
    # Save out of order: the ledger must come back oldest first regardless.
    for transaction in (stranger, second, first):
        repository.save(transaction)

    ledger = repository.get_by_wallet_id(wallet_id)

    assert [t.transaction_id for t in ledger] == [
        first.transaction_id,
        second.transaction_id,
    ]


def test_get_by_wallet_id_is_empty_for_a_wallet_without_transactions():
    repository = InMemoryTransactionRepository()

    assert repository.get_by_wallet_id(uuid4()) == []


def test_get_by_provider_reference_finds_a_saved_transaction():
    repository = InMemoryTransactionRepository()
    transaction = Transaction(
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="payment-1",
        provider_reference="prov-123",
    )
    repository.save(transaction)

    result = repository.get_by_provider_reference("prov-123")

    assert result is transaction


def test_get_by_provider_reference_returns_none_when_not_found():
    repository = InMemoryTransactionRepository()

    result = repository.get_by_provider_reference("prov-missing")

    assert result is None

