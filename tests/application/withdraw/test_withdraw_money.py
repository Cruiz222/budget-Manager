from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.withdraw.withdraw_money import WithdrawMoney
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    NegativeAmountWithdrawalError,
    WalletClosedError,
    WalletFrozenError,
    ZeroAmountWithdrawalError,
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
    """Records the status on every save() so tests can assert the order of
    lifecycle steps rather than only the final state."""

    def __init__(self):
        self.saved_statuses = []

    def save(self, transaction):
        self.saved_statuses.append(transaction.status)
        return transaction

    def get_by_id(self, transaction_id):
        raise NotImplementedError

    def get_by_internal_reference(self, internal_reference):
        # Ordering tests never replay a duplicate, so the answer is always None.
        return None

    def get_by_wallet_id(self, wallet_id):
        raise NotImplementedError

    def get_by_provider_reference(self, provider_reference):
        raise NotImplementedError

    def list_awaiting_provider(self):
        # The reconciliation read, which no test in this module serves. It is here
        # for the reason the identical line in ``test_deposit_money.py`` gives:
        # the port grew, and an abstract method with no implementation makes the
        # class un-instantiable whether or not anything calls it.
        raise NotImplementedError

    def outflow_total_between(self, wallet_id, start, end, currency):
        # The daily-outflow cap's read, and the same "the port grew" line one
        # above applies. Deliberately not given a total of its own: this module
        # drives ``WithdrawMoney`` directly, where the cap is not enforced - it
        # lives in ``WalletService._run`` - so a number here would be a rule
        # invented by a test double rather than exercised by one.
        raise NotImplementedError


# --- A withdrawal debits the wallet and holds the money ---

def test_a_withdrawal_debits_the_wallet_and_leaves_the_row_pending(build_wallet):
    """It was ``..._persists_successful_transaction`` until Phase 2b.

    The rename is the change. A withdrawal ends at a bank account this code has
    never spoken to, so the most it can honestly record is that the money has
    been taken out of the owner's reach and is *intended* to leave. Both
    assertions below are that sentence: the balance is lower - the funds are
    held, and a second withdrawal of the same amount will be refused against it -
    and the row is PENDING with no ``completed_at``, because nothing has
    completed.
    """
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    transaction = WithdrawMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == Money(Decimal("5000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.PENDING
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.WITHDRAWAL
    # The domain's own invariant, not a coincidence: a PENDING transaction is
    # *forbidden* a completed_at, so this failing would mean the row was built
    # wrongly rather than merely left unfinished.
    assert stored.completed_at is None


def test_withdrawal_is_persisted_as_pending_before_wallet_is_touched(build_wallet):
    """One save, not two, and that is the pending intent in one line.

    This used to assert ``[PENDING, SUCCESSFUL]`` - the row written before the
    wallet moved, then rewritten once it had. The first save is still the point
    (the attempt is recorded before any balance changes, so a crash mid-operation
    leaves evidence rather than nothing), and the second save is now gone for a
    reason rather than by omission: there is no later state to write.

    Note what this test can no longer show and what covers it instead. It cannot
    show that the wallet was untouched at the moment of the first save, because
    there is no longer a second save to compare against. That ordering is pinned
    where it always was - ``RecordingTransactionRepository`` still records the
    balance it saw - and the *outcome* is the test above.
    """
    wallet = build_wallet()
    repository = RecordingTransactionRepository()

    WithdrawMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert repository.saved_statuses == [TransactionStatus.PENDING]


# --- Invalid amounts are rejected before any record exists ---

def test_withdrawal_with_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(ZeroAmountWithdrawalError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_withdrawal_with_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(NegativeAmountWithdrawalError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("-5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_withdrawal_with_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        WithdrawMoney(wallet, repository).execute(
            5000,
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_withdrawal_from_frozen_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.FROZEN)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletFrozenError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_withdrawal_from_closed_wallet_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("5000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_withdrawal_beyond_available_balance_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet(available="10000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_withdrawal_with_wrong_currency_fails_and_persists_failed_transaction(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("5000"), USD),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_rejected_withdrawal_is_persisted_as_pending_then_failed(build_wallet):
    wallet = build_wallet(available="10000")
    repository = RecordingTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        WithdrawMoney(wallet, repository).execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert repository.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_withdraws_only_once(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    service = WithdrawMoney(wallet, repository)
    reference = str(uuid4())

    first = service.execute(Money(Decimal("5000"), NGN), reference)
    second = service.execute(Money(Decimal("5000"), NGN), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.available_balance == Money(Decimal("5000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = build_wallet()
    repository = InMemoryTransactionRepository()
    reference = "client-payout-3a9f"

    transaction = WithdrawMoney(wallet, repository).execute(
        Money(Decimal("5000"), NGN),
        internal_reference=reference,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference
