from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus
from app.domain.money.exception import (
    WalletAlreadyFrozenError,
    WalletAlreadyClosedError
)

def test_freeze_wallet():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(10000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    wallet.freeze()

    assert wallet.status == WalletStatus.FROZEN


def test_freezing_already_frozen_wallet_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.FROZEN,
        _available_balance=Money(10000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(WalletAlreadyFrozenError):
        wallet.freeze()    


def test_freezing_closed_wallet_raises_error():
    wallet = Wallet (
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.CLOSED,
        _available_balance=Money(10000, Currency.NGN),
        _locked_balance=Money(4000, Currency.NGN)
    )

    with pytest.raises(WalletAlreadyClosedError):
        wallet.freeze()   



