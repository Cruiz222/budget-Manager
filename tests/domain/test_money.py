from decimal import Decimal
import pytest
from typing import Any

# Adjust these imports according to your module structure
from domain.money.currency import CurrencyCode
from domain.money.exception import (
    CurrencyMismatchError,
    InvalidMoneyOperationError,
    MoneyError,
    UnsupportedCurrencyError,
    UnsupportedDecimalPlaceError,
    ZeroDivisionError,
)
from domain.money.money import Money


# --- Fixtures ---
@pytest.fixture
def usd_100():
    return Money(Decimal("100.00"), CurrencyCode.USD)


@pytest.fixture
def usd_50():
    return Money(Decimal("50.00"), CurrencyCode.USD)


@pytest.fixture
def ngn_100():
    return Money(Decimal("100.00"), CurrencyCode.NGN)


# --- 1. Initialization & Validation Tests ---
def test_money_creation_success(usd_100: Any):
    assert usd_100.amount == Decimal("100.00")
    assert usd_100.currency == CurrencyCode.USD


def test_money_allows_negative_amount():
    overdraft = Money(Decimal("-50.25"), CurrencyCode.USD)
    assert overdraft.amount == Decimal("-50.25")
    assert overdraft.is_debt is True


def test_unsupported_currency_raises_error():
    with pytest.raises(UnsupportedCurrencyError):
        Money(Decimal("10.00"), "INVALID_CURRENCY") # type: ignore


def test_more_than_two_decimal_places_raises_error():
    with pytest.raises(UnsupportedDecimalPlaceError):
        Money(Decimal("10.555"), CurrencyCode.USD)


# --- 2. Helper Properties & Methods Tests ---
def test_is_debt_property(usd_100: Any):
    debt = Money(Decimal("-10.00"), CurrencyCode.USD)
    assert debt.is_debt is True
    assert usd_100.is_debt is False


def test_abs_method():
    debt = Money(Decimal("-50.00"), CurrencyCode.USD)
    assert debt.abs() == Money(Decimal("50.00"), CurrencyCode.USD)


def test_unary_negation(usd_100: Any):
    negated = -usd_100
    assert negated == Money(Decimal("-100.00"), CurrencyCode.USD)


# --- 3. Arithmetic Operations Tests ---
def test_addition_success(usd_100: Any, usd_50: Any):
    result = usd_100 + usd_50
    assert result == Money(Decimal("150.00"), CurrencyCode.USD)


def test_addition_currency_mismatch_raises_error(usd_100: Any, ngn_100: Any):
    with pytest.raises(CurrencyMismatchError):
        _ = usd_100 + ngn_100


def test_addition_invalid_type_raises_error(usd_100: Any):
    with pytest.raises(InvalidMoneyOperationError):
        _ = usd_100 + Decimal("50.00")


def test_subtraction_success(usd_100: Any, usd_50: Any):
    result = usd_100 - usd_50
    assert result == Money(Decimal("50.00"), CurrencyCode.USD)


def test_subtraction_resulting_in_negative(usd_50: Any, usd_100: Any):
    result = usd_50 - usd_100
    assert result == Money(Decimal("-50.00"), CurrencyCode.USD)


def test_multiplication_success(usd_50: Any):
    assert usd_50 * 2 == Money(Decimal("100.00"), CurrencyCode.USD)
    assert 2 * usd_50 == Money(Decimal("100.00"), CurrencyCode.USD)
    assert usd_50 * Decimal("1.5") == Money(Decimal("75.00"), CurrencyCode.USD)


def test_multiplication_invalid_type_raises_error(usd_50: Any):
    with pytest.raises(InvalidMoneyOperationError):
        _ = usd_50 * "two"


def test_division_success(usd_100: Any):
    result = usd_100 / 2
    assert result == Money(Decimal("50.00"), CurrencyCode.USD)


def test_division_by_zero_raises_error(usd_100: Any):
    with pytest.raises(ZeroDivisionError):
        _ = usd_100 / 0


def test_division_invalid_type_raises_error(usd_100: Any):
    with pytest.raises(InvalidMoneyOperationError):
        _ = usd_100 / "two"


# --- 4. Comparison Operators Tests ---
def test_equality(usd_100: Any):
    same_usd_100 = Money(Decimal("100.00"), CurrencyCode.USD)
    different_amount = Money(Decimal("50.00"), CurrencyCode.USD)
    different_currency = Money(Decimal("100.00"), CurrencyCode.NGN)

    assert usd_100 == same_usd_100
    assert usd_100 != different_amount
    assert usd_100 != different_currency
    assert usd_100 != "100 USD"  # Comparing with non-Money object evaluates to False


def test_relational_comparisons(usd_50: Any, usd_100: Any):
    assert usd_50 < usd_100
    assert usd_50 <= usd_100
    assert usd_100 > usd_50
    assert usd_100 >= usd_50


def test_comparison_currency_mismatch_raises_error(usd_100: Any, ngn_100: Any):
    with pytest.raises(CurrencyMismatchError):
        _ = usd_100 > ngn_100


# --- 5. String & Representation Tests ---
def test_string_representation(usd_100: Any):
    assert str(usd_100) == "100.00 USD"


def test_repr_representation(usd_100: Any):
    assert (
        repr(usd_100)
        == "Money(amount=Decimal('100.00'), currency=CurrencyCode.USD)"
    )


# --- 6. Exception Hierarchy Verification ---
def test_exceptions_inherit_from_money_error():
    """Ensures all custom errors can be caught via the base MoneyError class."""
    with pytest.raises(MoneyError):
        Money(Decimal("10.00"), "INVALID") # type: ignore

    with pytest.raises(MoneyError):
        Money(Decimal("10.00"), CurrencyCode.USD) / 0 # type: ignore