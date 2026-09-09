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


def test_transaction_amount_can_not_be_changed_directly():
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
        transaction.amount = Money(100, Currency.NGN)



def test_wallet_id_can_not_be_changed_directly():
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
        transaction.wallet_id = uuid4()


def test_transaction_id_can_not_be_changed_directly():
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
        transaction.transaction_id = uuid4()


def test_transaction_type_can_not_be_changed_directly():
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
        transaction.type = TransactionType.WITHDRAW



def test_internal_reference_can_not_be_changed_directly():
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
        transaction.internal_reference = "reference"



def test_provider_reference_can_not_be_changed_directly():
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
        transaction.provider_reference = "reference-provision"



def test_transaction_created_at_can_not_be_changed_directly():
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
        transaction.created_at = datetime.now()



def test_transaction_completed_at_can_not_be_changed_directly():
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
        transaction.completed_at = datetime.now()



def test_transaction_reversed_at_can_not_be_changed_directly():
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
        reversed_at=None
    )

    with pytest.raises(AttributeError):
        transaction.reversed_at = datetime.now()


def test_transaction_changed_can_not_be_changed_directly():
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
        narration="narration"
    )

    with pytest.raises(AttributeError):
        transaction.narration = "narration-type"        









