from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.domain.money.currency import Currency
from app.domain.money.money import Money
from app.domain.money.exception import (
    CurrencyMismatchError,
    InvalidMoneyOperationError,
    UnsupportedCurrencyError,
    UnsupportedDecimalPlaceError,
    ZeroDivisionError,
)

NGN = Currency.NGN
USD = Currency.USD


# --- Creation & validation ---

def test_money_with_decimal_amount_is_valid():
    money = Money(Decimal("5000"), NGN)

    assert money.amount == Decimal("5000")
    assert money.currency is NGN


def test_money_with_int_amount_is_canonicalised_to_decimal():
    money = Money(5000, NGN)

    assert isinstance(money.amount, Decimal)
    assert money.amount == Decimal("5000")


def test_money_with_two_decimal_places_is_valid():
    money = Money(Decimal("5000.50"), NGN)

    assert money.amount == Decimal("5000.50")


def test_money_with_float_amount_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(0.1, NGN)


def test_money_with_bool_amount_is_rejected():
    # bool is a subclass of int, so it needs an explicit check
    with pytest.raises(InvalidMoneyOperationError):
        Money(True, NGN)


def test_money_with_string_amount_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money("5000", NGN)


def test_money_with_nan_amount_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("NaN"), NGN)


def test_money_with_infinite_amount_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("Infinity"), NGN)


def test_money_with_more_than_two_decimal_places_is_rejected():
    with pytest.raises(UnsupportedDecimalPlaceError):
        Money(Decimal("5000.001"), NGN)


def test_money_with_unsupported_currency_is_rejected():
    with pytest.raises(UnsupportedCurrencyError):
        Money(Decimal("5000"), "NGN")


def test_money_is_immutable():
    money = Money(Decimal("5000"), NGN)

    with pytest.raises(FrozenInstanceError):
        money.amount = Decimal("9000")


def test_money_can_be_negative():
    money = Money(Decimal("-50.25"), NGN)

    assert money.amount == Decimal("-50.25")


# --- Addition ---

def test_adding_same_currency_returns_correct_sum():
    result = Money(Decimal("100"), NGN) + Money(Decimal("50.50"), NGN)

    assert result == Money(Decimal("150.50"), NGN)


def test_addition_returns_a_new_object_and_leaves_operands_unchanged():
    a = Money(Decimal("100"), NGN)
    b = Money(Decimal("50"), NGN)

    result = a + b

    assert result is not a
    assert result is not b
    assert a.amount == Decimal("100")
    assert b.amount == Decimal("50")


def test_adding_different_currencies_raises():
    with pytest.raises(CurrencyMismatchError):
        Money(Decimal("100"), NGN) + Money(Decimal("100"), USD)


def test_adding_non_money_raises():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("100"), NGN) + 100


# --- Subtraction ---

def test_subtracting_same_currency_returns_correct_difference():
    result = Money(Decimal("100"), NGN) - Money(Decimal("25.50"), NGN)

    assert result == Money(Decimal("74.50"), NGN)


def test_subtracting_different_currencies_raises():
    with pytest.raises(CurrencyMismatchError):
        Money(Decimal("100"), NGN) - Money(Decimal("100"), USD)


def test_subtraction_can_produce_a_negative_result():
    result = Money(Decimal("50"), NGN) - Money(Decimal("100"), NGN)

    assert result == Money(Decimal("-50"), NGN)


# --- Multiplication ---

def test_multiplying_by_int_works():
    result = Money(Decimal("100.50"), NGN) * 3

    assert result == Money(Decimal("301.50"), NGN)


def test_right_multiplying_by_int_works():
    result = 3 * Money(Decimal("100"), NGN)

    assert result == Money(Decimal("300"), NGN)


def test_multiplying_by_decimal_works():
    result = Money(Decimal("100"), NGN) * Decimal("2.5")

    assert result == Money(Decimal("250"), NGN)


def test_multiplying_by_float_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("100"), NGN) * 2.5


def test_multiplying_money_by_money_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("100"), NGN) * Money(Decimal("2"), NGN)


def test_multiplying_too_many_decimal_places_is_rejected():
    with pytest.raises(UnsupportedDecimalPlaceError):
        Money(Decimal("1.05"), NGN) * Decimal("1.05")


# --- Division ---

def test_dividing_by_int_works():
    result = Money(Decimal("100"), NGN) / 4

    assert result == Money(Decimal("25"), NGN)


def test_dividing_by_decimal_works():
    result = Money(Decimal("100"), NGN) / Decimal("2.50")

    assert result == Money(Decimal("40"), NGN)


def test_dividing_by_zero_raises():
    with pytest.raises(ZeroDivisionError):
        Money(Decimal("100"), NGN) / 0


def test_dividing_money_by_money_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("100"), NGN) / Money(Decimal("2"), NGN)


def test_dividing_by_float_is_rejected():
    with pytest.raises(InvalidMoneyOperationError):
        Money(Decimal("100"), NGN) / 2.5


# --- Equality & hashing ---

def test_equal_amounts_in_same_currency_are_equal():
    assert Money(5000, NGN) == Money(Decimal("5000.00"), NGN)


def test_equal_amounts_in_different_currencies_are_not_equal():
    assert Money(Decimal("5000"), NGN) != Money(Decimal("5000"), USD)


def test_money_is_not_equal_to_non_money():
    assert (Money(Decimal("5000"), NGN) == 5000) is False


def test_money_can_be_a_dictionary_key():
    prices = {Money(5000, NGN): "sandals"}

    assert prices[Money(Decimal("5000.00"), NGN)] == "sandals"


# --- Comparisons ---

def test_comparing_same_currency_is_allowed():
    small = Money(Decimal("100"), NGN)
    large = Money(Decimal("200"), NGN)

    assert small < large
    assert small <= large
    assert large > small
    assert large >= small
    assert small >= Money(Decimal("100"), NGN)


def test_comparing_different_currencies_raises():
    ngn = Money(Decimal("100"), NGN)
    usd = Money(Decimal("100"), USD)

    with pytest.raises(CurrencyMismatchError):
        ngn < usd
    with pytest.raises(CurrencyMismatchError):
        ngn > usd
    with pytest.raises(CurrencyMismatchError):
        ngn <= usd
    with pytest.raises(CurrencyMismatchError):
        ngn >= usd


# --- Helpers ---

def test_is_debt_is_true_for_negative_amount():
    assert Money(Decimal("-50"), NGN).is_debt is True


def test_is_debt_is_false_for_positive_amount():
    assert Money(Decimal("50"), NGN).is_debt is False


def test_abs_returns_a_positive_new_money_with_same_currency():
    debt = Money(Decimal("-50.25"), NGN)

    absolute = debt.abs()

    assert absolute == Money(Decimal("50.25"), NGN)
    assert absolute is not debt
