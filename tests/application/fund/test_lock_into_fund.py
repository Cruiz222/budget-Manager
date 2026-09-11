"""``LockIntoFund``: available money moved into a named pot.

The pot-scoped successor to the old ``LockFunds`` tests, and mostly a
translation - the amount validation, the PENDING/FAILED audit trail and the
idempotency key are the base class's behaviour and none of that changed. What is
new is at the bottom: the operation now has a *pot* to name, and the way it
fails when the pot does not exist is a genuinely different shape from every
other failure in this file.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.fund.lock_into_fund import LockIntoFund
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotFoundError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
)
from app.domain.money.fundKind import FundKind
from app.domain.money.money import Money
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.walletStatus import WalletStatus
from app.infrastructure.repositories.in_memory_transaction_repository import (
    InMemoryTransactionRepository,
)

NGN = Currency.NGN
USD = Currency.USD


def a_wallet_with_a_pot(
    build_wallet, name="Vacation", status=WalletStatus.ACTIVE, **kwargs
):
    """A wallet with one open pot, which is what every case here needs.

    ``build_wallet``'s ``locked=`` builds a pot called "Locked"; naming one here
    keeps these tests describing a handful of pots rather than a locked balance
    wearing a pot's clothes.

    The status is applied *after* the pot is opened, because ``open_fund``
    refuses a closed wallet - the same ordering the ``build_wallet`` fixture
    itself has to use, and for the same reason.
    """
    wallet = build_wallet(**kwargs)
    wallet.open_fund(name, FundKind.PERSONAL)
    wallet.status = status
    return wallet


# --- Successful lock ---

def test_a_lock_moves_available_money_into_the_named_pot(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    transaction = LockIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert wallet.available_balance == Money(Decimal("7000"), NGN)
    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("3000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.wallet_id == wallet.wallet_id
    assert stored.type is TransactionType.LOCK_FUNDS
    assert stored.completed_at is not None


def test_the_ledger_row_names_the_pot(build_wallet):
    """``fund_id`` is what makes the movement auditable months later.

    "LOCK_FUNDS 3000" is the old row and it says the money moved; it cannot say
    *where*, which was tolerable while there was one answer and is not now that
    there are several. The pot's identity is on the row for the same reason a
    payout's destination is: the ledger should not need the wallet's current
    state to be read.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    transaction = LockIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN), internal_reference=str(uuid4())
    )

    assert transaction.fund_id == wallet.fund_by_name("Vacation").fund_id


def test_lock_is_persisted_as_pending_before_wallet_is_touched(
    build_wallet, recording_transactions
):
    wallet = a_wallet_with_a_pot(build_wallet)

    LockIntoFund(wallet, recording_transactions, "Vacation").execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.SUCCESSFUL,
    ]


# --- Invalid amounts are rejected before any record exists ---

def test_a_zero_amount_fails_and_persists_nothing(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("0"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)
    assert not repository.transactions


def test_a_negative_amount_fails_and_persists_nothing(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("-3000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert not repository.transactions


def test_a_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            3000,
            internal_reference=str(uuid4()),
        )

    assert not repository.transactions


# --- Wallet rejections leave a FAILED audit record ---

def test_locking_more_than_the_available_balance_fails(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, available="10000")
    repository = InMemoryTransactionRepository()

    with pytest.raises(InsufficientFundsError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_locking_into_a_closed_wallet_fails(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("3000"), NGN),
            internal_reference=str(uuid4()),
        )

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_locking_the_wrong_currency_fails(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        LockIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("3000"), USD),
            internal_reference=str(uuid4()),
        )

    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("0"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_a_rejected_lock_is_persisted_as_pending_then_failed(
    build_wallet, recording_transactions
):
    wallet = a_wallet_with_a_pot(build_wallet, available="10000")

    with pytest.raises(InsufficientFundsError):
        LockIntoFund(wallet, recording_transactions, "Vacation").execute(
            Money(Decimal("15000"), NGN),
            internal_reference=str(uuid4()),
        )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_locks_only_once(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()
    service = LockIntoFund(wallet, repository, "Vacation")
    reference = str(uuid4())

    first = service.execute(Money(Decimal("3000"), NGN), reference)
    second = service.execute(Money(Decimal("3000"), NGN), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.locked_balance == Money(Decimal("3000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()
    reference = "lock-reservation-9c21"

    transaction = LockIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN),
        internal_reference=reference,
    )

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.internal_reference == reference


# --- The pot the operation was pointed at ---

def test_locking_into_an_unknown_pot_is_refused_before_anything_is_recorded(
    build_wallet,
):
    """A different failure shape from every other one in this file, on purpose.

    The pot is resolved in the constructor, so an unknown name is raised by
    ``LockIntoFund(...)`` itself - before ``execute`` has validated the amount
    and before any PENDING row exists. There is consequently **no FAILED audit
    row** for this case, and that is right rather than an oversight: nothing was
    ever attempted against the wallet, so a row saying an attempt failed would be
    a small lie in the ledger.

    Compare ``test_locking_more_than_the_available_balance_fails`` above, where
    the wallet really was asked and really did refuse, and where the row exists
    for exactly that reason.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotFoundError):
        LockIntoFund(wallet, repository, "Holiday")

    assert not repository.transactions
    assert wallet.available_balance == Money(Decimal("10000"), NGN)


def test_a_lock_lands_in_the_named_pot_and_not_in_another(build_wallet):
    """The pot is chosen, not fallen into.

    With two pots open, the money must arrive in the one that was named - the
    failure this guards against is an operation that finds "a" pot, and the
    balance totals would look perfectly correct afterwards if it did.
    """
    wallet = build_wallet(available="10000")
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.open_fund("Salary", FundKind.PERSONAL)

    LockIntoFund(wallet, InMemoryTransactionRepository(), "Salary").execute(
        Money(Decimal("4000"), NGN), internal_reference=str(uuid4())
    )

    assert wallet.fund_by_name("Salary").balance == Money(Decimal("4000"), NGN)
    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("0"), NGN)
