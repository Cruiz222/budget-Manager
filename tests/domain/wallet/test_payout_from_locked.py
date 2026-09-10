from decimal import Decimal

import pytest

from app.domain.money.currency import Currency
from app.domain.money.exception import (
    CurrencyMismatchError,
    InsufficientFundsError,
    InvalidAmountError,
    WalletClosedError,
    WalletFrozenError,
)
from app.domain.money.money import Money
from app.domain.money.walletStatus import WalletStatus

NGN = Currency.NGN
USD = Currency.USD


def test_payout_from_locked_spends_locked_and_leaves_available_alone(build_wallet):
    """The defining difference from release_funds.

    release_funds moves locked -> available, so total holdings are unchanged.
    A payout *spends* the locked balance: locked falls, available is untouched,
    and what the wallet holds in total drops.
    """
    wallet = build_wallet(available="4000", locked="10000")

    wallet.payout_from_locked(Money(Decimal("6000"), NGN))

    assert wallet.locked_balance == Money(Decimal("4000"), NGN)
    assert wallet.available_balance == Money(Decimal("4000"), NGN)


def test_payout_from_locked_reduces_total_holdings(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")

    wallet.payout_from_locked(Money(Decimal("2000"), NGN))

    total = wallet.available_balance + wallet.locked_balance
    assert total == Money(Decimal("4000"), NGN)


def test_payout_exactly_the_locked_balance_empties_it(build_wallet):
    wallet = build_wallet(available="1000", locked="5000")

    wallet.payout_from_locked(Money(Decimal("5000"), NGN))

    assert wallet.locked_balance == Money(Decimal("0"), NGN)
    assert wallet.available_balance == Money(Decimal("1000"), NGN)


def test_payout_more_than_locked_cannot_reach_into_available(build_wallet):
    """Available balance is not a fallback - a payout is funded by locked only."""
    wallet = build_wallet(available="10000", locked="500")

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(Money(Decimal("5000"), NGN))


def test_payout_insufficient_locked_leaves_both_balances_untouched(build_wallet):
    wallet = build_wallet(available="1000", locked="4000")

    with pytest.raises(InsufficientFundsError):
        wallet.payout_from_locked(Money(Decimal("4001"), NGN))

    assert wallet.locked_balance == Money(Decimal("4000"), NGN)
    assert wallet.available_balance == Money(Decimal("1000"), NGN)


def test_payout_zero_amount_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(InvalidAmountError):
        wallet.payout_from_locked(Money(Decimal("0"), NGN))


def test_payout_negative_amount_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(InvalidAmountError):
        wallet.payout_from_locked(Money(Decimal("-2000"), NGN))


def test_payout_currency_mismatch_error(build_wallet):
    wallet = build_wallet(locked="4000")

    with pytest.raises(CurrencyMismatchError):
        wallet.payout_from_locked(Money(Decimal("2000"), USD))


def test_payout_from_closed_wallet_error(build_wallet):
    wallet = build_wallet(status=WalletStatus.CLOSED, locked="4000")

    with pytest.raises(WalletClosedError):
        wallet.payout_from_locked(Money(Decimal("2000"), NGN))


def test_payout_from_frozen_wallet_error(build_wallet):
    """Freezing stops value leaving the wallet.

    lock_funds and release_funds still work on a frozen wallet because they only
    move money between balances. A payout is money leaving, so it is refused -
    the same call withdraw() makes.
    """
    wallet = build_wallet(status=WalletStatus.FROZEN, locked="4000")

    with pytest.raises(WalletFrozenError):
        wallet.payout_from_locked(Money(Decimal("2000"), NGN))
