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
    InvalidMetaData
   
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


