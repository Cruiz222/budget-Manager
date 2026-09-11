"""``DepositIntoFund``: money arriving from outside, straight into a pot.

The operation the original request was about. "I locked 50,000 and want to keep
adding to it without opening a new plan for every deposit" is
``test_a_sealed_pot_keeps_accepting_deposits`` below, and the rest of the file is
what surrounds it: the ledger row, the audit trail, and the two statuses the
wallet treats differently on the way in.

One thing to hold onto while reading: a deposit and a lock are not the same
move, and this file and ``test_lock_into_fund.py`` are the two halves of that
distinction. A deposit brings money *into* the wallet; a lock moves money that
is already in it. Both can end in a pot.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.fund.deposit_into_fund import DepositIntoFund
from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    FundNotFoundError,
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
    build_wallet, name="Vacation", maturity_date=None, status=WalletStatus.ACTIVE, **kwargs
):
    """See the same helper in ``test_lock_into_fund``: the status goes on after
    the pot is opened, because ``open_fund`` refuses a closed wallet."""
    wallet = build_wallet(**kwargs)
    wallet.open_fund(name, FundKind.PERSONAL, maturity_date=maturity_date)
    wallet.status = status
    return wallet


def test_a_deposit_lands_in_the_pot_and_leaves_available_alone(build_wallet):
    """The wallet's total grows, and it grows in exactly one place.

    Asserting both halves on purpose: a deposit that reached the pot by way of
    the available balance would leave the pot looking right and the available
    balance looking larger than it should, and the wallet's *total* would still
    add up. The two assertions together are what rule that out.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    transaction = DepositIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN),
        internal_reference=str(uuid4()),
    )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("3000"), NGN)
    assert wallet.available_balance == Money(Decimal("10000"), NGN)
    assert wallet.locked_balance == Money(Decimal("3000"), NGN)

    stored = repository.get_by_id(transaction.transaction_id)
    assert stored.status is TransactionStatus.SUCCESSFUL
    assert stored.type is TransactionType.DEPOSIT
    assert stored.fund_id == wallet.fund_by_name("Vacation").fund_id
    assert stored.completed_at is not None


def test_a_deposit_is_recorded_as_one_movement_not_two(build_wallet):
    """One ``DEPOSIT`` row, not a ``DEPOSIT`` and a ``LOCK_FUNDS``.

    The money never touched the available balance, so there is no second move to
    record. Writing two rows would be describing a journey the money did not
    take - and it would double what a statement-reading owner thinks happened.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    DepositIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN), internal_reference=str(uuid4())
    )

    types = [row.type for row in repository.transactions.values()]
    assert types == [TransactionType.DEPOSIT]


def test_a_sealed_pot_keeps_accepting_deposits(build_wallet):
    """The headline case, and the reason funds exist.

    A pot with a maturity date refuses money *leaving*, never money arriving.
    Before the pot existed, the only way to add to a locked sum was to lock
    again, and the request was to stop having to do that "for each deposit".
    """
    wallet = a_wallet_with_a_pot(
        build_wallet, maturity_date=date(2026, 6, 1), available="0"
    )
    repository = InMemoryTransactionRepository()

    for amount in ("50000", "2000", "1500"):
        DepositIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal(amount), NGN), internal_reference=str(uuid4())
        )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("53500"), NGN)
    # And it is still sealed - accepting money did not count as releasing it.
    assert wallet.locked_balance == Money(Decimal("53500"), NGN)


def test_a_deposit_into_a_matured_pot_is_allowed_too(build_wallet):
    """Nothing about the date gates a deposit, in either direction.

    Worth pinning separately from the sealed case: "deposits are unrestricted"
    is a claim about both sides of the maturity date, and a check that happened
    to allow past dates but refuse future ones would pass the test above.
    """
    wallet = a_wallet_with_a_pot(
        build_wallet, maturity_date=date(2020, 1, 1), available="0"
    )

    DepositIntoFund(wallet, InMemoryTransactionRepository(), "Vacation").execute(
        Money(Decimal("500"), NGN), internal_reference=str(uuid4())
    )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("500"), NGN)


def test_a_frozen_wallet_still_accepts_a_deposit(build_wallet):
    """Freezing stops value leaving, and this is value arriving.

    The old ``apply_deposit`` made the same call for the available balance, so
    this is continuity rather than a decision taken here.
    """
    wallet = a_wallet_with_a_pot(build_wallet, status=WalletStatus.FROZEN)

    DepositIntoFund(wallet, InMemoryTransactionRepository(), "Vacation").execute(
        Money(Decimal("3000"), NGN), internal_reference=str(uuid4())
    )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("3000"), NGN)


def test_depositing_into_a_closed_wallet_fails(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet, status=WalletStatus.CLOSED)
    repository = InMemoryTransactionRepository()

    with pytest.raises(WalletClosedError):
        DepositIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("3000"), NGN), internal_reference=str(uuid4())
        )

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


def test_depositing_the_wrong_currency_fails(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(CurrencyMismatchError):
        DepositIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal("3000"), USD), internal_reference=str(uuid4())
        )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("0"), NGN)

    stored = list(repository.transactions.values())[0]
    assert stored.status is TransactionStatus.FAILED


# --- Invalid amounts are rejected before any record exists ---

@pytest.mark.parametrize("amount", ["0", "-3000"])
def test_a_non_positive_deposit_fails_and_persists_nothing(build_wallet, amount):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositIntoFund(wallet, repository, "Vacation").execute(
            Money(Decimal(amount), NGN), internal_reference=str(uuid4())
        )

    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("0"), NGN)
    assert not repository.transactions


def test_a_non_money_amount_fails_and_persists_nothing(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(InvalidAmountError):
        DepositIntoFund(wallet, repository, "Vacation").execute(
            3000, internal_reference=str(uuid4())
        )

    assert not repository.transactions


def test_a_rejected_deposit_is_persisted_as_pending_then_failed(
    build_wallet, recording_transactions
):
    wallet = a_wallet_with_a_pot(build_wallet, status=WalletStatus.CLOSED)

    with pytest.raises(WalletClosedError):
        DepositIntoFund(wallet, recording_transactions, "Vacation").execute(
            Money(Decimal("3000"), NGN), internal_reference=str(uuid4())
        )

    assert recording_transactions.saved_statuses == [
        TransactionStatus.PENDING,
        TransactionStatus.FAILED,
    ]


# --- Idempotency ---

def test_replaying_the_same_internal_reference_deposits_only_once(build_wallet):
    """A retried deposit must not double the pot.

    The same guard covers every operation, but a deposit is where a duplicate
    would be most expensive to notice: nothing about the pot's later behaviour
    would look wrong, it would simply hold twice what its owner put in.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()
    service = DepositIntoFund(wallet, repository, "Vacation")
    reference = str(uuid4())

    first = service.execute(Money(Decimal("3000"), NGN), reference)
    second = service.execute(Money(Decimal("3000"), NGN), reference)

    assert second.transaction_id == first.transaction_id
    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("3000"), NGN)


def test_caller_supplied_internal_reference_is_persisted(build_wallet):
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()
    reference = "salary-topup-4f10"

    transaction = DepositIntoFund(wallet, repository, "Vacation").execute(
        Money(Decimal("3000"), NGN), internal_reference=reference
    )

    assert repository.get_by_id(transaction.transaction_id).internal_reference == reference


# --- The pot the operation was pointed at ---

def test_depositing_into_an_unknown_pot_is_refused_before_anything_is_recorded(
    build_wallet,
):
    """Resolved in the constructor, so there is no PENDING row and no FAILED one.

    Nothing was attempted against the wallet, so nothing belongs in the ledger.
    See the equivalent case in ``test_lock_into_fund`` for the comparison with a
    rejection the wallet actually made.
    """
    wallet = a_wallet_with_a_pot(build_wallet)
    repository = InMemoryTransactionRepository()

    with pytest.raises(FundNotFoundError):
        DepositIntoFund(wallet, repository, "Holiday")

    assert not repository.transactions


def test_a_deposit_lands_in_the_named_pot_and_not_in_another(build_wallet):
    wallet = build_wallet(available="0")
    wallet.open_fund("Vacation", FundKind.PERSONAL)
    wallet.open_fund("Salary", FundKind.PERSONAL)

    DepositIntoFund(wallet, InMemoryTransactionRepository(), "Salary").execute(
        Money(Decimal("4000"), NGN), internal_reference=str(uuid4())
    )

    assert wallet.fund_by_name("Salary").balance == Money(Decimal("4000"), NGN)
    assert wallet.fund_by_name("Vacation").balance == Money(Decimal("0"), NGN)
