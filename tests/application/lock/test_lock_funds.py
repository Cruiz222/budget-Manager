from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.lock.lock_funds import LockFunds
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.wallet import Wallet
from app.domain.money.walletStatus import WalletStatus
from app.domain.repositories.transaction_repository import TransactionRepository
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN
USD = Currency.USD


class RecordingTransactionRepository(TransactionRepository):
    def __init__(self):
        self.saved_statuses = []

    def save(self, transaction):
        self.saved_statuses.append(transaction.status)
        return transaction

    def get_by_id(self, transaction_id):
        raise NotImplementedError

    def get_by_internal_reference(self, internal_reference):
        return None

    def get_by_wallet_id(self, wallet_id):
        raise NotImplementedError

    def get_by_provider_reference(self, provider_reference):
        raise NotImplementedError


# --- Successful lock ---

def test_successful_lock_moves_available_to_locked_and_persists_successful_transaction(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    transaction = LockFunds(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == Money(Decimal("7000"), NGN)
    assert wallet.locked_balance == Money(Decimal("3000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.LOCK_FUNDS
    assert stored.completed_at is not None


def test_lock_is_persisted_as_pending_before_wallet_is_touched(build_wallet):
    wallet = build_wallet()
    repository = RecordingTransactionRepository()

    LockFunds(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


# --- Invalid amounts are rejected before any record exists ---

def test_lock_with_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)
    assert not repository.transactions


def test_lock_with_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("-3000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_lock_with_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockFunds(wallet, repository).execute(
            3000,
            internal_reference=str(uuid4()),
        )

    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_lock_more_than_available_balance_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(available="10000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_lock_into_closed_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
        )

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_lock_with_wrong_currency_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("3000"), USD),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_rejected_lock_is_persisted_as_pending_then_failed(build_wallet):
    wallet = build_wallet(available="10000")
    repository = RecordingTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        LockFunds(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_locks_only_once(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    service = LockFunds(wallet, repository)
    reference = str(uuid4())

    first = service.execute(Money(Decimal("3000"), NGN), reference)
    second = service.execute(Money(Decimal("3000"), NGN), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.locked_balance == Money(Decimal("3000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    reference = "lock-reservation-9c21"

    transaction = LockFunds(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=reference,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference
