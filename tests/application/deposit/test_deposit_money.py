from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.deposit.deposit_money import DepositMoney
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
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


def build_wallet(
    status=WalletStatus.ACTIVE,
    available="10000",
    currency=NGN,
):
    return Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        status=status,
        _available_balance=Money(Decimal(available), currency),
        _locked_balance=Money(Decimal("0"), currency),
        currency=currency,
    )


class RecordingTransactionRepository(TransactionRepository):
    """Test double that records the status each time save() is called.

    Lets us assert the ORDER of lifecycle steps, not just the final state -
    e.g. that the attempt is persisted as PENDING before the wallet is touched.
    """

    def __init__(self):
        self.saved_statuses = []

    def save(self, transaction):
        self.saved_statuses.append(transaction.status)
        return transaction

    def get_by_id(self, transaction_id):
        raise NotImplementedError


# --- Successful deposit ---

def test_successful_deposit_increases_balance_and_persists_successful_transaction():
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    transaction = DepositMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN)
    )

    assert wallet.available_balance == Money(Decimal("15000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.DEPOSIT
    assert stored.completed_at is not None


def test_deposit_is_persisted_as_pending_before_wallet_is_touched():
    wallet = build_wallet()
    repository = RecordingTransactionRepository()

    DepositMoney(wallet, repository).execute(Money(Decimal("5000"), NGN))

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


# --- Invalid amounts are rejected before any record exists ---

def test_deposit_with_zero_amount_fails_and_persists_nothing():
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(Money(Decimal("0"), NGN))

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_deposit_with_negative_amount_fails_and_persists_nothing():
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(Money(Decimal("-5000"), NGN))

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_deposit_with_non_money_amount_fails_and_persists_nothing():
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(5000)

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_deposit_with_wrong_currency_fails_and_persists_failed_transaction():
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        DepositMoney(wallet, repository).execute(Money(Decimal("5000"), USD))

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED
    assert stored.completed_at is not None


def test_deposit_into_closed_wallet_fails_and_persists_failed_transaction():
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        DepositMoney(wallet, repository).execute(Money(Decimal("5000"), NGN))

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED
    assert stored.completed_at is not None


def test_rejected_deposit_is_persisted_as_pending_then_failed():
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = RecordingTransactionRepository()

    with pytest.raises(WalletClosedError):
        DepositMoney(wallet, repository).execute(Money(Decimal("5000"), NGN))

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]
