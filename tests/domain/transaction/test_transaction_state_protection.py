from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money


def test_transaction_status_can_not_be_changed_directly():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),
        completed_at=None,

    )
    
    with pytest.raises(AttributeError):
        transaction.status = TransactionStatus.FAILED


def test_newly_created_transaction_must_start_with_status_pending():
    transaction = Transaction (
        transaction_id=uuid4(),
        wallet_id=uuid4(),
        type=TransactionType.DEPOSIT,
        amount=Money(5000, Currency.NGN),
        internal_reference="internal_reference",
        provider_reference="provider_reference",
        metadata={},
        created_at=datetime.now(),
        completed_at=None,

    )        

    assert transaction.status == TransactionStatus.PENDING