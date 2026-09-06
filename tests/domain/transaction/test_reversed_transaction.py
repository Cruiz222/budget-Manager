from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.exception import (
    InvalidTransactionStateError,
    TransactionAlreadyReversedError
)

def test_successful_transaction_gets_reversed_and_completed_at():
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

    transaction.mark_successful()

    successful_completed_at = transaction.completed_at

    transaction.reverse()

    assert transaction.status == TransactionStatus.REVERSED
    assert transaction.completed_at == successful_completed_at


def test_pending_transaction_cannot_be_reversed():
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

    with pytest.raises(InvalidTransactionStateError):
        transaction.reverse()


def test_reversing_transaction_already_reversed_raises_error():
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

    transaction.mark_successful()
    transaction.reverse()

    with pytest.raises(TransactionAlreadyReversedError):
       transaction.reverse()


def test_failed_transaction_cannot_be_reversed():
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

    transaction.mark_failed()

    with pytest.raises(InvalidTransactionStateError):
        transaction.reverse()        
