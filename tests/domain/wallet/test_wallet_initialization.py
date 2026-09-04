from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
   InvalidWalletCurrencyError,
   InvalidWalletStatusError,
   InvalidWalletAvailableBalanceError,
   InvalidWalletLockedBalanceError,
   CurrencyMismatchError
)


def test_wallet_with_invalid_currency_raises_error():
    with pytest.raises(InvalidWalletCurrencyError):
        Wallet(
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency="OMG",
            status=WalletStatus.FROZEN,
            _available_balance=Money(10000, Currency.NGN),
            _locked_balance=Money(4000, Currency.NGN),
        )


def test_wallet_with_invalid_status_raises_error():
    with pytest.raises(InvalidWalletStatusError):
        wallet = Wallet (
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status="active",
           _available_balance=Money(5000, Currency.NGN),
           _locked_balance=Money(6000, Currency.NGN),
    )  


def test_wallet_with_invalid_available_balance_raises_error():
    with pytest.raises(InvalidWalletAvailableBalanceError):
        Wallet (
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=5000,
            _locked_balance=Money(5000, Currency.NGN)
        )


def test_wallet_with_invalid_locked_balance_raises_error():
    with pytest.raises(InvalidWalletLockedBalanceError):
        Wallet (
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _locked_balance=5000
        )        

       
def test_available_balance_currency_must_match_wallet_currency():
    with pytest.raises(CurrencyMismatchError):
        Wallet (
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.USD),
            _locked_balance=Money(6000, Currency.NGN)
        )    


def test_locked_balance_currency_must_match_wallet_currency():
    with pytest.raises(CurrencyMismatchError):
        Wallet (
            wallet_id=uuid4(),
            user_id=uuid4(),
            currency=Currency.NGN,
            status=WalletStatus.ACTIVE,
            _available_balance=Money(5000, Currency.NGN),
            _locked_balance=Money(6000, Currency.USD)
        )  
