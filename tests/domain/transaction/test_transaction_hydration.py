from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    InvalidTransactionDateStamp,
    InvalidTransactionStatusError,
    MoneyError,
    TransactionAlreadySuccessfulError,
)
from app.domain.money.money import Money
from app.domain.money.transaction import Transaction
from app.domain.money.transactionStatus import TransactionStatus
from app.domain.money.transactionType import TransactionType


def make_transaction(**overrides):
    """Build a valid PENDING transaction and let tests override any field.

    Keeps hydration tests focused on lifecycle state instead of re-typing the
    full set of required arguments in every test.
    """
    params = {
        "wallet_id": uuid4(),
        "type": TransactionType.DEPOSIT,
        "amount": Money(Decimal("5000"), Currency.NGN),
        "internal_reference": "internal-reference",
    }
    params.update(overrides)
    return Transaction(**params)


# --- Default (new transaction) path still works without a status ---

def test_omitting_status_defaults_to_pending():
    transaction = make_transaction()

    assert transaction.status is TransactionStatus.PENDING
    assert transaction.completed_at is None
    assert transaction.reversed_at is None


# --- Hydrating terminal states from storage ---

def test_hydrate_successful_transaction():
    completed_at = datetime(2026, 9, 1, 10, 30, 0)

    transaction = make_transaction(
        status=TransactionStatus.SUCCESSFUL,
        completed_at=completed_at,
    )

    assert transaction.status is TransactionStatus.SUCCESSFUL
    assert transaction.completed_at == completed_at
    assert transaction.reversed_at is None


def test_hydrate_failed_transaction():
    completed_at = datetime(2026, 9, 1, 10, 30, 0)

    transaction = make_transaction(
        status=TransactionStatus.FAILED,
        completed_at=completed_at,
    )

    assert transaction.status is TransactionStatus.FAILED
    assert transaction.completed_at == completed_at


def test_hydrate_reversed_transaction():
    completed_at = datetime(2026, 9, 1, 10, 30, 0)
    reversed_at = datetime(2026, 9, 2, 9, 0, 0)

    transaction = make_transaction(
        status=TransactionStatus.REVERSED,
        completed_at=completed_at,
        reversed_at=reversed_at,
    )

    assert transaction.status is TransactionStatus.REVERSED
    assert transaction.completed_at == completed_at
    assert transaction.reversed_at == reversed_at


def test_hydration_restores_identifiers_and_facts():
    wallet_id = uuid4()
    transaction_id = uuid4()
    created_at = datetime(2026, 8, 15, 12, 0, 0)

    transaction = make_transaction(
        wallet_id=wallet_id,
        transaction_id=transaction_id,
        created_at=created_at,
        status=TransactionStatus.SUCCESSFUL,
        completed_at=datetime(2026, 8, 15, 12, 5, 0),
    )

    assert transaction.wallet_id == wallet_id
    assert transaction.transaction_id == transaction_id
    assert transaction.created_at == created_at


# --- Terminal states are only valid with their required timestamps ---

def test_successful_without_completed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(status=TransactionStatus.SUCCESSFUL)


def test_failed_without_completed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(status=TransactionStatus.FAILED)


def test_reversed_without_completed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(
            status=TransactionStatus.REVERSED,
            reversed_at=datetime(2026, 9, 2, 9, 0, 0),
        )


def test_reversed_without_reversed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(
            status=TransactionStatus.REVERSED,
            completed_at=datetime(2026, 9, 1, 10, 30, 0),
        )


def test_pending_with_reversed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(reversed_at=datetime(2026, 9, 2, 9, 0, 0))


def test_successful_with_reversed_at_raises():
    with pytest.raises(InvalidTransactionDateStamp):
        make_transaction(
            status=TransactionStatus.SUCCESSFUL,
            completed_at=datetime(2026, 9, 1, 10, 30, 0),
            reversed_at=datetime(2026, 9, 2, 9, 0, 0),
        )


def test_invalid_status_type_raises():
    with pytest.raises(InvalidTransactionStatusError):
        make_transaction(status="successful")


# --- A hydrated object is still guarded by domain methods ---

def test_hydrated_successful_transaction_cannot_be_marked_successful_again():
    transaction = make_transaction(
        status=TransactionStatus.SUCCESSFUL,
        completed_at=datetime(2026, 9, 1, 10, 30, 0),
    )

    with pytest.raises(TransactionAlreadySuccessfulError):
        transaction.mark_successful()


def test_hydrated_successful_transaction_can_still_be_reversed():
    transaction = make_transaction(
        status=TransactionStatus.SUCCESSFUL,
        completed_at=datetime(2026, 9, 1, 10, 30, 0),
    )

    transaction.reverse()

    assert transaction.status is TransactionStatus.REVERSED
    assert transaction.reversed_at is not None


# --- Regression: InvalidTransactionDateStamp must be a domain error ---

def test_invalid_transaction_date_stamp_is_a_money_error():
    # Was incorrectly subclassing the built-in MemoryError, which meant
    # `except MoneyError` would never catch it.
    assert issubclass(InvalidTransactionDateStamp, MoneyError)
    assert not issubclass(InvalidTransactionDateStamp, MemoryError)
