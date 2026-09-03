from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
    InvalidAmountError,
    NegativeAmountDepositError,
    CurrencyMismatchError,
    WalletClosedError
)


def test_depositing_positive_amount_increases_available_balance():
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
    wallet.apply_deposit(Money(2000, Currency.NGN))

    # Assert
    assert wallet.available_balance.amount == 7000



def test_depositing_zero_amount_raises_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)

    )
    with pytest.raises(InvalidAmountError):
        wallet.apply_deposit(Money(0, Currency.NGN))

 
def test_depositing_negative_amount_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )        

    with pytest.raises(InvalidAmountError):
        wallet.apply_deposit(Money(-2000, Currency.NGN))


def test_depositing_different_currency_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )        

    with pytest.raises(CurrencyMismatchError):
        wallet.apply_deposit(Money(2000, Currency.USD))    

def test_depositing_into_closed_wallet_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.CLOSED,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN)
    )  

    with pytest.raises(WalletClosedError):
        wallet.apply_deposit(Money(2000, Currency.NGN))         


def test_depositing_into_frozen_wallet_is_allowed():
    # Arrange
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.FROZEN,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(0, Currency.NGN),
    )

    # Act
    wallet.apply_deposit(Money(2000, Currency.NGN))

    # Assert
    assert wallet.available_balance.amount == 7000  


def test_locked_balance_returns_locked_balance():    
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(5000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN),
    )


    result = wallet.locked_balance

    assert result == Money(4000, Currency.NGN)       