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
        status=TransactionStatus.SUCCESSFUL,
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="",
        provider_reference="",
        metadata={},
        created_at=datetime.now(),
        completed_at=datetime.now(),

    )

    transaction.reverse()

    assert transaction.status == TransactionStatus.REVERSED

    original_time_stamp = transaction.completed_at
    assert transaction.completed_at == original_time_stamp



def test_pending_transaction_cannot_be_reversed():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        status=TransactionStatus.PENDING,
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="",
        provider_reference="",
        metadata={},
        created_at=datetime.now(),
        completed_at=datetime.now(),

    )

    with pytest.raises(InvalidTransactionStateError):
        transaction.reverse()


def test_reversing_transaction_already_reversed_raises_error():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        status=TransactionStatus.REVERSED,
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="",
        provider_reference="",
        metadata={},
        created_at=datetime.now(),
        completed_at=datetime.now(),

    )

    with pytest.raises(TransactionAlreadyReversedError):
        transaction.reverse()


def test_failed_transaction_cannot_be_reversed():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        status=TransactionStatus.FAILED,
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="",
        provider_reference="",
        metadata={},
        created_at=datetime.now(),
        completed_at=datetime.now(),

    )

    with pytest.raises(InvalidTransactionStateError):
        transaction.reverse()        
