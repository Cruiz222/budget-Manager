from uuid import uuid4
import pytest
from datetime import datetime
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType
from app.domain.money.transaction import Transaction
from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.exception import (
    InvalidTransactionWalletIDError,
    InvalidTransactionTypeError,
    InvalidTransactionAmountError,
    InvalidInternalReference,
    InvalidproviderReference,
    InvalidTransactionNarration,
    InvalidMetaData,
    InvalidTransactionDateStamp
   
)

def test_creating_transaction_with_invalid_wallet_id_raises_error():
    with pytest.raises(InvalidTransactionWalletIDError):
        Transaction(
            wallet_id=str,
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference="test-reference",
        )


def test_creating_transaction_with_invalid_type_raises_error():
    with pytest.raises(InvalidTransactionTypeError):
        Transaction(
            wallet_id=uuid4(),
            type="deposit",
            amount=Money(5000, Currency.NGN),
            internal_reference="test-reference",
        )


def test_creating_transaction_with_invalid_amount_raises_error():
    with pytest.raises(InvalidTransactionAmountError):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=5000,
            internal_reference="test-reference",
        )


def test_creating_transaction_with_zero_amount_raises_error():
    with pytest.raises(InvalidTransactionAmountError):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(0, Currency.NGN),
            internal_reference="test-reference",
        )


def test_creating_transaction_with_negative_amount_raises_error():
    with pytest.raises(InvalidTransactionAmountError):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(-5000, Currency.NGN),
            internal_reference="test-reference",
        )


def test_creating_transaction_with_empty_internal_reference_raises_error():
    with pytest.raises(InvalidInternalReference):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            internal_reference="",
        )


def test_creating_transaction_with_provider_reference_must_be_a_string():
    with pytest.raises(InvalidproviderReference):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference=1234567890,
            internal_reference="internal-reference"
        )        


def test_creating_transaction_with_provider_reference_as_none_is_valid():
    transaction = Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference=None,
            internal_reference="internal-reference"
        )     

    transaction.internal_reference = None

    assert transaction.internal_reference == None   


def test_creating_transaction_with_narration_must_be_a_string():
    with pytest.raises(InvalidTransactionNarration):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration=int
        )      
def test_creating_transaction_with_narration_must_be_a_string():
    with pytest.raises(InvalidTransactionNarration):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration=int
        ) 

def test_creating_transaction_with_narration_as_none_is_valid():
    transaction = Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal reference",
            narration=None
        )     

    transaction.narration = None

    assert transaction.narration == None   



def test_creating_transaction_with_invalid_metadata_raises_error():
    with pytest.raises(InvalidMetaData):
        Transaction(
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration="narration",
            metadata="metadata"
        )                


def test_creating_pending_transaction_must_have_completed_at_as_none():
    with pytest.raises(InvalidTransactionDateStamp):
        Transaction (
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration="narration",
            completed_at=datetime.now()
        ) 


def test_successful_transaction_must_not_have_completed_at_as_none():
    with pytest.raises(InvalidTransactionDateStamp):
        Transaction (
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.SUCCESSFUL,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration="narration",
            completed_at=None
        )         


def test_failed_transaction_must_not_have_completed_at_as_none():
    with pytest.raises(InvalidTransactionDateStamp):
        Transaction (
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.FAILED,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration="narration",
            completed_at=None
        )         


def test_reversed_transaction_must_not_have_completed_at_as_none():
    with pytest.raises(InvalidTransactionDateStamp):
        Transaction (
            wallet_id=uuid4(),
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.REVERSED,
            amount=Money(5000, Currency.NGN),
            provider_reference="provider-reference",
            internal_reference="internal-reference",
            narration="narration",
            completed_at=None
        )         

