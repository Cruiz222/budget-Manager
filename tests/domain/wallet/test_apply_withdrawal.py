from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
    InsufficientFundsError,
    WalletFrozenError,
    WalletClosedError,
    ZeroAmountWithdrawalError,
    NegativeAmountWithdrawalError,
    CurrencyMismatchError
)

def test_withdrawing_positive_amount_reduces_available_balance():
    # Arrange
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN),
    )

    # Act
    wallet.withdraw(Money(2000, Currency.NGN))

    # Assert
    assert wallet.available_balance.amount == 3000


def test_withdrawing_more_than_available_balance_raises_insufficient_funds():
    # Arrange
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN),
    )

    # Act & Assert
    with pytest.raises(InsufficientFundsError):
        wallet.withdraw(Money(6000, Currency.NGN)) 


def test_withdrawing_from_frozen_wallet_raises_error():

    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.FROZEN,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )

    with pytest.raises(WalletFrozenError):
        wallet.withdraw(Money(2000, Currency.NGN))


def test_withdrawing_from_closed_wallet_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.CLOSED,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )

    with pytest.raises(WalletClosedError):
        wallet.withdraw(Money(1000, Currency.NGN))


def test_withdrawing_zero_amount_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )

    with pytest.raises(ZeroAmountWithdrawalError):
        wallet.withdraw(Money(0, Currency.NGN))


def test_withdrawing_negative_amount_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )

    with pytest.raises(NegativeAmountWithdrawalError):
        wallet.withdraw(Money(-1000, Currency.NGN))


def test_withdrawing_mismatched_currency_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )

    with pytest.raises(CurrencyMismatchError):
        wallet.withdraw(Money(1000, Currency.USD))        


        