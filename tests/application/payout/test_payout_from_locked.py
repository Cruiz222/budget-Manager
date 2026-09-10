from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.payout.payout_from_locked import PayoutFromLocked
from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    MissingDestinationError,
    WalletClosedError,
    WalletFrozenError,
)
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.domain.repositories.transaction_repository import TransactionRepository
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN
USD = Currency.USD

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)


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


# --- Successful payout ---

def test_successful_payout_spends_locked_and_persists_successful_transaction(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")
    repository = InMemoryTransactionRepository()

    transaction = PayoutFromLocked(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    assert wallet.locked_balance == Money(Decimal("2000"), NGN)
    assert wallet.available_balance == Money(Decimal("1000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.PAYOUT
    assert stored.completed_at is not None


def test_the_destination_is_recorded_on_the_ledger_entry(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    transaction = PayoutFromLocked(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.destination == DESTINATION


def test_payout_is_persisted_as_pending_before_wallet_is_touched(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = RecordingTransactionRepository()

    PayoutFromLocked(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
    )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


# --- A payout must say where the money went ---

def test_payout_without_a_destination_is_rejected_and_persists_nothing(build_wallet):
    """The rule lives on the Transaction, so it fires before anything is saved."""
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(MissingDestinationError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


# --- Invalid amounts are rejected before any record exists ---

def test_payout_with_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


def test_payout_with_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("-3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert not repository.transactions


def test_payout_with_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        PayoutFromLocked(wallet, repository).execute(
            3000,
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_payout_more_than_locked_balance_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(available="10000", locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)
    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_from_closed_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED, locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_from_frozen_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.FROZEN, locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletFrozenError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_payout_with_wrong_currency_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("3000"), USD),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert wallet.locked_balance == Money(Decimal("5000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_rejected_payout_is_persisted_as_pending_then_failed(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = RecordingTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        PayoutFromLocked(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
            destination=DESTINATION,
        )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_pays_only_once(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()
    service = PayoutFromLocked(wallet, repository)
    reference = str(uuid4())

    first = service.execute(
        Money(Decimal("3000"), NGN), reference, destination=DESTINATION
    )
    second = service.execute(
        Money(Decimal("3000"), NGN), reference, destination=DESTINATION
    )

    assert second.transaction_id == first.transaction_id
    assert wallet.locked_balance == Money(Decimal("2000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = build_wallet(locked="5000")
    repository = InMemoryTransactionRepository()
    reference = "salary-run-sep-005"

    transaction = PayoutFromLocked(wallet, repository).execute(
        Money(Decimal("3000"), NGN),
        internal_reference=reference,
        destination=DESTINATION,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference
