from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
    InsufficientFundsError,
    InvalidAmountError,
    CurrencyMismatchError,
    WalletClosedError
)

def test_release_funds():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(10000, Currency.NGN)
    )

    wallet.release_funds(Money(6000, Currency.NGN))

    assert wallet._available_balance.amount == 10000

    assert wallet._locked_balance.amount == 4000



def test_release_insufficient_funds_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(InsufficientFundsError):
        wallet.release_funds(Money(5000, Currency.NGN))



def test_release_zero_amount_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(InvalidAmountError):
        wallet.release_funds(Money(0, Currency.NGN))


def test_release_negative_amount_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(InvalidAmountError):
        wallet.release_funds(Money(-2000, Currency.NGN))        


def test_release_currency_mismatch_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(CurrencyMismatchError):
        wallet.release_funds(Money(2000, Currency.USD))  



def test_release_frozen_wallet_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.FROZEN,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    wallet.release_funds(Money(2000, Currency.NGN))  

    assert wallet._available_balance.amount == 6000

    assert wallet._locked_balance.amount == 2000    



def test_release_closed_wallet_error():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.CLOSED,
        _available_balance=Money(4000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(WalletClosedError):
        wallet.release_funds(Money(2000, Currency.NGN))                       