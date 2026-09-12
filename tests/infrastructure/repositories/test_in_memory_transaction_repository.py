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


# --- the reconciliation read ------------------------------------------------


def test_list_awaiting_provider_returns_pending_rows_that_carry_a_reference():
    """**Both halves of the filter, and neither is decorative.**

    The reconciler may only ask a provider about rows a provider was actually
    told about, so the read is PENDING **and** provider-backed: the status half
    excludes a finished deposit, and the reference half excludes every CLI
    withdrawal and plan-run payout, which carry no reference because nothing
    outside ``InitiateDeposit`` ever writes one.
    """
    repository = InMemoryTransactionRepository()

    def build_transaction(created_at, provider_reference=None):
        return Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            provider_reference=provider_reference,
            created_at=created_at,
        )

    awaiting = build_transaction(datetime(2026, 1, 2), "prov-awaiting")
    finished = build_transaction(datetime(2026, 1, 2), "prov-finished")
    finished.mark_successful()
    unsponsored = build_transaction(datetime(2026, 1, 2))
    for transaction in (awaiting, finished, unsponsored):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.transaction_id for t in found] == [awaiting.transaction_id]


def test_list_awaiting_provider_is_oldest_first():
    """The order is the contract, because the batch takes from the front.

    Saved out of order, so a repository that returned insertion order would fail
    here - which is the same assertion ``get_by_wallet_id``'s ordering test makes
    one method over, and it is the property that stops the oldest stuck payment
    from being starved by a backlog longer than one batch.
    """
    repository = InMemoryTransactionRepository()

    def build_transaction(created_at):
        return Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference=str(uuid4()),
            provider_reference=f"prov-{created_at.hour}",
            created_at=created_at,
        )

    oldest = build_transaction(datetime(2026, 1, 2, 11))
    middle = build_transaction(datetime(2026, 1, 2, 12))
    newest = build_transaction(datetime(2026, 1, 2, 13))
    for transaction in (newest, oldest, middle):
        repository.save(transaction)

    found = repository.list_awaiting_provider()

    assert [t.transaction_id for t in found] == [
        oldest.transaction_id,
        middle.transaction_id,
        newest.transaction_id,
    ]


def test_list_awaiting_provider_is_empty_when_nothing_is_in_flight():
    repository = InMemoryTransactionRepository()

    assert repository.list_awaiting_provider() == []

