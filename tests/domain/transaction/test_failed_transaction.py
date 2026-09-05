from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.exception import (
    TransactionAlreadyFailedError,
    InvalidTransactionStateError
)

def test_pending_transaction_failed_and_completed_at():
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

    transaction.mark_failed()

    assert transaction.status == TransactionStatus.FAILED

    assert transaction.completed_at is not None


def test_pending_transaction_already_failed_raises_error():
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

    with pytest.raises(TransactionAlreadyFailedError):
        transaction.mark_failed()




def test_successful_transaction_cannot_be_marked_failed():
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

    with pytest.raises(InvalidTransactionStateError):
        transaction.mark_failed()



def test_reversed_transaction_cannot_be_marked_failed():
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

    with pytest.raises(InvalidTransactionStateError):
        transaction.mark_failed()



