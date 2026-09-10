from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.payout.payout_from_available import PayoutFromAvailable
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


def pay(wallet, repository, amount, **overrides):
    return PayoutFromAvailable(wallet, repository).execute(
        Money(Decimal(amount), NGN),
        internal_reference=overrides.pop("internal_reference", str(uuid4())),
        destination=overrides.pop("destination", DESTINATION),
    )


class TestTheMoneyMoves:
    """The mirrored half of payout_from_locked - spend available, leave locked alone."""

    def test_spends_available_and_leaves_locked_alone(self, build_wallet):
        wallet = build_wallet(available="5000", locked="4000")

        pay(wallet, InMemoryTransactionRepository(), "2000")

        assert wallet.available_balance == Money(Decimal("3000"), NGN)
        assert wallet.locked_balance == Money(Decimal("4000"), NGN)

    def test_reduces_total_holdings(self, build_wallet):
        """Value left the wallet - unlike release, which only reshuffles it."""
        wallet = build_wallet(available="5000", locked="4000")

        pay(wallet, InMemoryTransactionRepository(), "2000")

        total = wallet.available_balance + wallet.locked_balance
        assert total == Money(Decimal("7000"), NGN)

    def test_paying_out_everything_empties_available(self, build_wallet):
        wallet = build_wallet(available="5000", locked="4000")

        pay(wallet, InMemoryTransactionRepository(), "5000")

        assert wallet.available_balance == Money(Decimal("0"), NGN)
        assert wallet.locked_balance == Money(Decimal("4000"), NGN)

    def test_locked_balance_is_not_a_fallback(self, build_wallet):
        """Reserved money stays reserved - a payout from available cannot reach it."""
        wallet = build_wallet(available="500", locked="10000")

        with pytest.raises(InsufficientFundsError):
            pay(wallet, InMemoryTransactionRepository(), "5000")

        assert wallet.available_balance == Money(Decimal("500"), NGN)
        assert wallet.locked_balance == Money(Decimal("10000"), NGN)


class TestTheLedgerEntry:
    def test_recorded_as_payout_not_withdrawal(self, build_wallet):
        """The destination is what makes it a PAYOUT. That is the whole widening."""
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        transaction = pay(wallet, repository, "2000")

        stored = repository.get_by_id(transaction.transaction_id)
        assert stored.type is TransactionType.PAYOUT
        assert stored.status is TransactionStatus.SUCCESSFUL

    def test_the_destination_is_recorded(self, build_wallet):
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        transaction = pay(wallet, repository, "2000")

        assert repository.get_by_id(transaction.transaction_id).destination == DESTINATION

    def test_a_payout_without_a_destination_is_rejected(self, build_wallet):
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        with pytest.raises(MissingDestinationError):
            PayoutFromAvailable(wallet, repository).execute(
                Money(Decimal("2000"), NGN),
                internal_reference=str(uuid4()),
            )

        assert wallet.available_balance == Money(Decimal("5000"), NGN)
        assert not repository.transactions


class TestRejections:
    def test_insufficient_available_leaves_the_balance_untouched(self, build_wallet):
        wallet = build_wallet(available="5000")

        with pytest.raises(InsufficientFundsError):
            pay(wallet, InMemoryTransactionRepository(), "5001")

        assert wallet.available_balance == Money(Decimal("5000"), NGN)

    def test_a_rejection_still_leaves_a_failed_audit_row(self, build_wallet):
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        with pytest.raises(InsufficientFundsError):
            pay(wallet, repository, "5001")

        stored = list(repository.transactions.values())[0]
        assert stored.status is TransactionStatus.FAILED

    def test_a_frozen_wallet_refuses_a_payout(self, build_wallet):
        """Freezing stops value from leaving, and a payout is value leaving."""
        wallet = build_wallet(status=WalletStatus.FROZEN, available="5000")

        with pytest.raises(WalletFrozenError):
            pay(wallet, InMemoryTransactionRepository(), "2000")

    def test_a_closed_wallet_refuses_a_payout(self, build_wallet):
        wallet = build_wallet(status=WalletStatus.CLOSED, available="5000")

        with pytest.raises(WalletClosedError):
            pay(wallet, InMemoryTransactionRepository(), "2000")

    def test_zero_amount_is_rejected_before_any_record_exists(self, build_wallet):
        """InvalidAmountError, not ZeroAmountWithdrawalError.

        withdraw() has its own zero/negative vocabulary, but the WalletOperation
        base class rejects those amounts before _apply() runs - so routing this
        operation through wallet.withdraw() cannot leak withdrawal wording.
        """
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        with pytest.raises(InvalidAmountError):
            pay(wallet, repository, "0")

        assert not repository.transactions

    def test_a_currency_mismatch_is_rejected(self, build_wallet):
        wallet = build_wallet(available="5000")
        repository = InMemoryTransactionRepository()

        with pytest.raises(CurrencyMismatchError):
            PayoutFromAvailable(wallet, repository).execute(
                Money(Decimal("500"), USD),
                internal_reference=str(uuid4()),
                destination=DESTINATION,
            )

        assert wallet.available_balance == Money(Decimal("5000"), NGN)


def test_replaying_the_same_reference_pays_only_once(build_wallet):
    wallet = build_wallet(available="5000")
    repository = InMemoryTransactionRepository()
    operation = PayoutFromAvailable(wallet, repository)
    reference = str(uuid4())

    first = operation.execute(
        Money(Decimal("2000"), NGN), reference, destination=DESTINATION
    )
    second = operation.execute(
        Money(Decimal("2000"), NGN), reference, destination=DESTINATION
    )

    assert second.transaction_id == first.transaction_id
    assert wallet.available_balance == Money(Decimal("3000"), NGN)
