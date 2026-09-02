from uuid import uuid4
import pytest
from app.domain.money.wallet import Wallet
from app.domain.money.money import Money
from app.domain.money.currency import Currency
from app.domain.money.walletStatus import WalletStatus

def test_lock_funds():
    wallet = Wallet(
        wallet_id=uuid4(),
        user_id=uuid4(),
        currency=Currency.NGN,
        status=WalletStatus.ACTIVE,
        _available_balance=Money(10000, Currency.NGN),
        _locked_balance=Money(6000, Currency.NGN)
    )

    wallet.lock_funds(Money(4000, Currency.NGN))

    assert wallet._available_balance.amount == 6000

    assert wallet._locked_balance.amount == 10000

    