from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.destination import Destination
from app.domain.money.destinationKind import DestinationKind
from app.domain.money.exception import (
    InvalidTransactionDestinationError,
    MissingDestinationError,
    UnexpectedDestinationError,
)
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType

NGN = Currency.NGN

DESTINATION = Destination(
    kind=DestinationKind.BANK_ACCOUNT,
    identifier="0123456789",
    name="Chinedu Okafor",
    details={"bank_code": "058"},
)

NON_PAYOUT_TYPES = [
    TransactionType.DEPOSIT,
    TransactionType.WITHDRAWAL,
    TransactionType.LOCK_FUNDS,
    TransactionType.UNLOCK_FUNDS,
]


def build_transaction(transaction_type=TransactionType.DEPOSIT, destination=None):
    return Transaction(
        wallet_id=uuid4(),
        type=transaction_type,
        amount=Money(Decimal("1000"), NGN),
        internal_reference=str(uuid4()),
        destination=destination,
    )


def test_payout_records_its_destination():
    transaction = build_transaction(TransactionType.PAYOUT, destination=DESTINATION)

    assert transaction.destination == DESTINATION
    assert transaction.destination.detail("bank_code") == "058"


def test_payout_without_a_destination_is_rejected():
    """A payout with nowhere to send the money is not a payable instruction."""
    with pytest.raises(MissingDestinationError):
        build_transaction(TransactionType.PAYOUT)


@pytest.mark.parametrize("transaction_type", NON_PAYOUT_TYPES)
def test_only_payouts_may_carry_a_destination(transaction_type):
    """The mirror rule: a destination on anything else is a bug, not a no-op."""
    with pytest.raises(UnexpectedDestinationError):
        build_transaction(transaction_type, destination=DESTINATION)


@pytest.mark.parametrize("transaction_type", NON_PAYOUT_TYPES)
def test_non_payouts_without_a_destination_are_fine(transaction_type):
    transaction = build_transaction(transaction_type)

    assert transaction.destination is None


def test_destination_must_be_a_destination_object():
    with pytest.raises(InvalidTransactionDestinationError):
        build_transaction(TransactionType.PAYOUT, destination="0123456789")


def test_a_hydrated_settled_payout_still_carries_its_destination():
    """Rehydrating from storage goes through the same constructor, so the rule
    holds for rows read back as well as rows first created."""
    transaction = Transaction(
        wallet_id=uuid4(),
        type=TransactionType.PAYOUT,
        amount=Money(Decimal("1000"), NGN),
        internal_reference=str(uuid4()),
        destination=DESTINATION,
        status=TransactionStatus.SUCCESSFUL,
        completed_at=datetime.now(),
    )

    assert transaction.destination == DESTINATION
