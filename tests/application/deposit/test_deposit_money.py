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

    def get_by_internal_reference(self, internal_reference):
        # DepositMoney asks this before saving; ordering tests never replay a
        # duplicate, so the answer is always None.
        return None

    def get_by_wallet_id(self, wallet_id):
        raise NotImplementedError

    def get_by_provider_reference(self, provider_reference):
        raise NotImplementedError

    def list_awaiting_provider(self):
        # The reconciliation read, which no test in this module serves. It is
        # here because ``TransactionRepository`` grew it: an abstract method with
        # no implementation in a subclass makes the subclass un-instantiable, so
        # this copy was broken by a change that has nothing to do with deposits.
        # That is the argument ``RecordingTransactionRepository`` in
        # ``tests/conftest.py`` already makes for consolidating these copies -
        # see its docstring, and note that this is the second time the port has
        # grown under one of them.
        raise NotImplementedError

    def outflow_total_between(self, wallet_id, start, end, currency):
        # The third time, and this one is the daily-outflow cap's read. Not
        # implemented here for the reason above and for one more: a deposit is
        # not an outflow, so no test in this module has a daily total to serve -
        # and a stub that returned a number would be this copy inventing a rule
        # about which rows count.
        raise NotImplementedError

    def pending_credit_total(self, wallet_id, currency):
        # The fourth time, and this one is closer to home than the third: it is
        # the *inbound* aggregate, and it is what ``InitiateDeposit._prepare``
        # adds to a projected balance before it opens a collection. Still not
        # implemented, because ``DepositMoney`` is the direct-credit door and
        # never asks - no provider sits in front of it, so there is no in-flight
        # money to count. See the port for that argument.
        raise NotImplementedError


# --- Successful deposit ---

def test_successful_deposit_increases_balance_and_persists_successful_transaction(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    transaction = DepositMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == Money(Decimal("15000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.DEPOSIT
    assert stored.completed_at is not None


def test_deposit_is_persisted_as_pending_before_wallet_is_touched(build_wallet):
    wallet = build_wallet()
    repository = RecordingTransactionRepository()

    DepositMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


# --- Invalid amounts are rejected before any record exists ---

def test_deposit_with_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_deposit_with_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(
            Money(Decimal("-5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_deposit_with_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositMoney(wallet, repository).execute(
            5000,
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_deposit_with_wrong_currency_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        DepositMoney(wallet, repository).execute(
            Money(Decimal("5000"), USD),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED
    assert stored.completed_at is not None


def test_deposit_into_closed_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        DepositMoney(wallet, repository).execute(
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED
    assert stored.completed_at is not None


def test_rejected_deposit_is_persisted_as_pending_then_failed(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = RecordingTransactionRepository()

    with pytest.raises(WalletClosedError):
        DepositMoney(wallet, repository).execute(
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_credits_only_once(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    service = DepositMoney(wallet, repository)
    reference = str(uuid4())

    first = service.execute(Money(Decimal("5000"), NGN), reference)
    second = service.execute(Money(Decimal("5000"), NGN), reference)

    # Same logical deposit returned - not a new one, and not double-credited.
    assert second.transaction_id == first.transaction_id
    assert wallet.available_balance == Money(Decimal("15000"), NGN)


def test_different_internal_references_are_both_credited(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    service = DepositMoney(wallet, repository)

    service.execute(Money(Decimal("5000"), NGN), str(uuid4()))
    service.execute(Money(Decimal("2000"), NGN), str(uuid4()))

    assert wallet.available_balance == Money(Decimal("17000"), NGN)
    assert len(repository.transactions) == 2


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    reference = "client-order-7f8a"

    transaction = DepositMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=reference,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference
