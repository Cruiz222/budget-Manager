from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.exception import (
    TransactionAlreadySuccessfulError
)

def test_pending_transaction_become_successful_and_completed_at():
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

    transaction.mark_successful()

    assert transaction.status == TransactionStatus.SUCCESSFUL

    assert transaction.completed_at is not None



def test_already_successful_transaction_raises_error():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        status=TransactionStatus.SUCCESSFUL,
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="",
        provider_reference="",
        metadata=dict,
        created_at=datetime.now(),
        completed_at=datetime.now(),

    )

    with pytest.raises(TransactionAlreadySuccessfulError):
        transaction.mark_successful()    